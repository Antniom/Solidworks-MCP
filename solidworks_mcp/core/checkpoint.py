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

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


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
        )

        key = f"{doc_title}::{name}"
        self._checkpoints[key] = meta

        return {
            "success": True,
            "message": f"Checkpoint '{name}' created at feature '{last_feat}' ({feat_count} features)",
            "checkpoint": meta.to_dict(),
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
        # Delete new features in reverse order
        for feat_obj in reversed(new_features):
            feat_name = feat_obj.Name
            try:
                ok = feat_obj.Select2(False, 0)
                if ok:
                    doc.EditDelete()
                    deleted_names.append(feat_name)
            except Exception as e:
                logger.warning(f"Error deleting feature '{feat_name}': {e}")

        # Rebuild document
        try:
            doc.ForceRebuild3(False)
        except Exception:
            pass

        return {
            "success": True,
            "message": f"Rolled back to checkpoint '{name}'. Removed {len(deleted_names)} features.",
            "deleted_features": deleted_names,
            "reverted_to": target_feat,
        }

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
