"""
CAD Checkpoint and Rollback Engine
-----------------------------------
Provides safe transactional modeling with named checkpoints,
feature rollback, and snapshot restore for SolidWorks documents.
"""

import os
import time
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field, asdict
from ..constants import SwErrors

logger = logging.getLogger(__name__)


@dataclass
class CheckpointMetadata:
    """Metadata for a modeling checkpoint."""
    name: str
    doc_title: str
    timestamp: float
    feature_count: int
    last_feature_name: str
    snapshot_path: Optional[str] = None
    description: str = ""
    # Parametric state captured at checkpoint time so rollback can restore values,
    # not just delete later features.
    dimension_values: Dict[str, float] = field(default_factory=dict)
    equations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["dimension_count"] = len(out.pop("dimension_values", {}) or {})
        out["equation_count"] = len(out.pop("equations", []) or [])
        return out


class CheckpointManager:
    """Manages feature tree checkpoints and snapshot rollbacks."""

    def __init__(self, checkpoints_dir: Optional[Path] = None):
        if checkpoints_dir is None:
            import tempfile
            base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
            checkpoints_dir = Path(base) / "solidworks-mcp" / "checkpoints"

        self.checkpoints_dir = Path(checkpoints_dir)
        self.checkpoints_dir.mkdir(parents=True, exist_ok=True)
        self._checkpoints: Dict[str, CheckpointMetadata] = {}

    def _get_feature_info(self, doc: Any) -> tuple[int, str]:
        """Counts features and identifies the latest feature name."""
        count = 0
        last_name = ""
        for feat in self._iter_features(doc):
            count += 1
            last_name = feat.Name
        return count, last_name

    def _iter_dimensions(self, doc: Any):
        """Yield (full_name, IDimension) for every display dimension in the feature tree."""
        from ..core.com_compat import get_attr_or_call
        seen = set()

        def _dims_of(f):
            try:
                disp = get_attr_or_call(f, "GetFirstDisplayDimension")
            except Exception:
                disp = None
            guard = 0
            while disp is not None and guard < 2000:
                guard += 1
                try:
                    d = get_attr_or_call(disp, "GetDimension")
                    full = getattr(d, "FullName", "") if d is not None else ""
                    if full and full not in seen:
                        seen.add(full)
                        yield full, d
                except Exception:
                    pass
                try:
                    disp = f.GetNextDisplayDimension(disp)
                except Exception:
                    break

        feat = getattr(doc, "FirstFeature", None)
        while feat is not None:
            yield from _dims_of(feat)
            try:
                sub = get_attr_or_call(feat, "GetFirstSubFeature")
                while sub is not None:
                    yield from _dims_of(sub)
                    sub = sub.GetNextSubFeature
            except Exception:
                pass
            try:
                feat = feat.GetNextFeature
            except Exception:
                break

    def _capture_dimensions(self, doc: Any) -> Dict[str, float]:
        values: Dict[str, float] = {}
        try:
            for full, d in self._iter_dimensions(doc):
                try:
                    values[full] = float(getattr(d, "SystemValue"))
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"Dimension capture warning: {e}")
        return values

    def _capture_equations(self, doc: Any) -> List[str]:
        from ..core.com_compat import get_attr_or_call
        eqs: List[str] = []
        try:
            eq_mgr = get_attr_or_call(doc, "GetEquationMgr")
            if eq_mgr is None:
                return eqs
            cnt = int(get_attr_or_call(eq_mgr, "GetCount") or 0)
            for i in range(cnt):
                eqs.append(str(eq_mgr.Equation(i) or ""))
        except Exception as e:
            logger.debug(f"Equation capture warning: {e}")
        return eqs

    def _restore_parametric_state(self, doc: Any, meta: "CheckpointMetadata") -> Dict[str, Any]:
        """Restore dimension values and equations recorded in the checkpoint."""
        from ..core.com_compat import get_attr_or_call
        restored_dims: List[str] = []
        failed_dims: List[str] = []
        restored_eqs = 0
        removed_eqs = 0

        # Equations first: they may drive dimensions.
        try:
            eq_mgr = get_attr_or_call(doc, "GetEquationMgr")
            if eq_mgr is not None:
                cnt = int(get_attr_or_call(eq_mgr, "GetCount") or 0)
                # Remove equations added after the checkpoint (from the end).
                for i in range(cnt - 1, len(meta.equations) - 1, -1):
                    try:
                        eq_mgr.Delete(i)
                        removed_eqs += 1
                    except Exception as e:
                        logger.debug(f"Equation delete warning at {i}: {e}")
                cnt = int(get_attr_or_call(eq_mgr, "GetCount") or 0)
                for i in range(min(cnt, len(meta.equations))):
                    try:
                        if str(eq_mgr.Equation(i) or "") != meta.equations[i]:
                            eq_mgr.SetEquationAndConfigurationOption(i, meta.equations[i], 1, None)
                            restored_eqs += 1
                    except Exception as e:
                        logger.debug(f"Equation restore warning at {i}: {e}")
        except Exception as e:
            logger.debug(f"Equation restore warning: {e}")

        try:
            for full, d in self._iter_dimensions(doc):
                if full not in meta.dimension_values:
                    continue
                want = meta.dimension_values[full]
                try:
                    have = float(getattr(d, "SystemValue"))
                    if abs(have - want) <= 1e-12:
                        continue
                    # 1 = swSetValue_InThisConfiguration
                    d.SetSystemValue3(want, 1, "")
                    restored_dims.append(full)
                except Exception as e:
                    failed_dims.append(full)
                    logger.debug(f"Dimension restore warning for {full}: {e}")
        except Exception as e:
            logger.debug(f"Dimension restore warning: {e}")

        return {
            "restored_dimensions": restored_dims,
            "failed_dimensions": failed_dims,
            "restored_equations": restored_eqs,
            "removed_equations": removed_eqs,
        }
    @staticmethod
    def _iter_features(doc: Any):
        """Walk the top-level feature tree (FirstFeature/GetNextFeature may be properties or methods)."""
        from ..core.com_compat import get_object_member
        feat = get_object_member(doc, "FirstFeature")
        while feat is not None:
            yield feat
            feat = get_object_member(feat, "GetNextFeature")

    @staticmethod
    def _snapshot_extension(doc: Any) -> str:
        """File extension matching the document type (a part snapshot of an assembly is unusable)."""
        from ..core.com_compat import get_attr_or_call
        try:
            ext = os.path.splitext(str(get_attr_or_call(doc, "GetPathName") or ""))[1].lower()
            if ext in (".sldprt", ".sldasm", ".slddrw"):
                return ext
            doc_type = int(get_attr_or_call(doc, "GetType"))
            return {1: ".sldprt", 2: ".sldasm", 3: ".slddrw"}.get(doc_type, ".sldprt")
        except Exception:
            return ".sldprt"

    def _normalize_title(self, doc: Any) -> str:
        """Get and normalize document title, stripping drawing sheet suffix and extensions."""
        from ..core.com_compat import get_attr_or_call
        raw = get_attr_or_call(doc, "GetTitle") or getattr(doc, "GetTitle", "Unknown")
        title = str(raw).split(" - ")[0].strip()
        return os.path.splitext(title)[0]

    def create_checkpoint(
        self,
        doc: Any,
        name: str,
        description: str = "",
        snapshot: bool = True,
    ) -> Dict[str, Any]:
        """
        Record a named modeling checkpoint for the active document.

        With snapshot=True a backup copy of the document is also written to the
        checkpoints directory (reported as snapshot_path) so the user can recover it by hand;
        rollback_checkpoint itself only removes features added after the checkpoint.
        
        Args:
            doc: Active IModelDoc2 object
            name: Identifier for the checkpoint
            description: Optional note
        """
        if doc is None:
            return {
                "success": False,
                "error": "No active document provided",
                "error_code": int(SwErrors.swNoActiveDocument),
                "error_name": "swNoActiveDocument",
            }

        doc_title = self._normalize_title(doc)
        feat_count, last_feat = self._get_feature_info(doc)

        # Snapshot file
        clean_title = "".join(c if c.isalnum() or c in "._-" else "_" for c in doc_title)
        clean_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)
        snapshot_filename = f"{clean_title}_{clean_name}_{int(time.time()*1000)}{self._snapshot_extension(doc)}"
        snapshot_path = str(self.checkpoints_dir / snapshot_filename)

        snapshot_saved = False
        if snapshot:
            try:
                res = doc.SaveCopyAs(snapshot_path)
                snapshot_saved = bool(res == 0 or os.path.exists(snapshot_path))
            except Exception as e:
                logger.debug(f"SaveCopyAs exception: {e}")

        meta = CheckpointMetadata(
            name=name,
            doc_title=doc_title,
            timestamp=time.time(),
            feature_count=feat_count,
            last_feature_name=last_feat,
            snapshot_path=snapshot_path if snapshot_saved else None,
            description=description,
            dimension_values=self._capture_dimensions(doc),
            equations=self._capture_equations(doc),
        )

        key = f"{doc_title}::{name}"
        replaced = key in self._checkpoints
        self._checkpoints[key] = meta

        msg = f"Checkpoint '{name}' created at feature '{last_feat}' ({feat_count} features)"
        if replaced:
            msg += f" — replaced an earlier checkpoint with the same name"
        return {
            "success": True,
            "message": msg,
            "checkpoint": meta.to_dict(),
            "replaced_existing": replaced,
        }

    def rollback_checkpoint(
        self,
        doc: Any,
        name: str,
    ) -> Dict[str, Any]:
        """
        Roll back the feature tree to the specified checkpoint state.
        Deletes any features created after the checkpoint was recorded.
        
        Args:
            doc: Active IModelDoc2 object
            name: Identifier of the checkpoint to restore
        """
        if doc is None:
            return {
                "success": False,
                "error": "No active document provided",
                "error_code": int(SwErrors.swNoActiveDocument),
                "error_name": "swNoActiveDocument",
            }

        doc_title = self._normalize_title(doc)
        key = f"{doc_title}::{name}"
        if key not in self._checkpoints:
            return {
                "success": False,
                "error": f"Checkpoint '{name}' not found for document '{doc_title}'",
                "error_code": int(SwErrors.swInvalidInput),
                "error_name": "swInvalidInput",
            }

        meta = self._checkpoints[key]
        target_feat = meta.last_feature_name

        # Gather features created after checkpoint
        new_features = []
        found_target = False
        for feat in self._iter_features(doc):
            if found_target:
                new_features.append(feat)
            elif feat.Name == target_feat:
                found_target = True

        if not found_target and target_feat:
            return {
                "success": False,
                "error": f"Target checkpoint feature '{target_feat}' no longer exists in feature tree",
            }

        deleted_names = []
        failed_deletes = []
        # Delete new features in reverse order
        for feat_obj in reversed(new_features):
            try:
                feat_name = feat_obj.Name
            except Exception:
                # Already removed together with its parent (absorbed sketch etc.)
                continue
            try:
                ok = feat_obj.Select2(False, 0)
                if ok:
                    doc.EditDelete()
                    deleted_names.append(feat_name)
                else:
                    failed_deletes.append(feat_name)
            except Exception as e:
                failed_deletes.append(feat_name)
                logger.warning(f"Error deleting feature '{feat_name}': {e}")

        # A feature that could not be selected may have gone with its parent; re-check the tree.
        if failed_deletes:
            remaining = set()
            f = getattr(doc, "FirstFeature", None)
            while f is not None:
                try:
                    remaining.add(f.Name)
                    f = f.GetNextFeature
                except Exception:
                    break
            failed_deletes = [n for n in failed_deletes if n in remaining]

        param_state = self._restore_parametric_state(doc, meta)

        # Rebuild document
        try:
            doc.ForceRebuild3(False)
        except Exception:
            pass

        n_dims = len(param_state["restored_dimensions"])
        n_eqs = param_state["restored_equations"] + param_state["removed_equations"]
        ok = not failed_deletes and not param_state["failed_dimensions"]
        msg = (
            f"Rolled back to checkpoint '{name}'. Removed {len(deleted_names)} features, "
            f"restored {n_dims} dimension value(s) and {n_eqs} equation change(s)."
        )
        if not ok:
            msg += (
                f" INCOMPLETE: could not delete {failed_deletes}; "
                f"could not restore {param_state['failed_dimensions']}."
            )
        result = {
            "success": ok,
            "message": msg,
            "deleted_features": deleted_names,
            "reverted_to": target_feat,
            **param_state,
        }
        if not ok:
            result["error"] = msg
            result["failed_deletes"] = failed_deletes
        return result

    def list_checkpoints(self, doc_title: Optional[str] = None) -> List[Dict[str, Any]]:
        """List active checkpoints, optionally filtered by document title."""
        results = []
        for key, meta in self._checkpoints.items():
            if doc_title is None or meta.doc_title.lower() == doc_title.lower():
                results.append(meta.to_dict())
        return results


_checkpoint_manager: Optional[CheckpointManager] = None

def get_checkpoint_manager() -> CheckpointManager:
    global _checkpoint_manager
    if _checkpoint_manager is None:
        _checkpoint_manager = CheckpointManager()
    return _checkpoint_manager
