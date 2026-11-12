"""
Post-Condition Verification Engine (F3)
---------------------------------------
Universal post-condition verification for all mutating CAD operations:
- Returned object is not None / False
- Feature / component / mate count increased
- Rebuild errors: fails only on NEW errors (pre-existing unrelated errors ignored)
- Mates: verifies mate's own feature error code and mate count increase
"""

import logging
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from ..core.com_compat import get_attr_or_call, byref_bool
from ..constants import SwErrors

logger = logging.getLogger("SolidWorksMCP.PostConditions")


@dataclass
class PreMutationState:
    feature_count: int = 0
    component_count: int = 0
    mate_count: int = 0
    rebuild_errors: List[Dict[str, Any]] = field(default_factory=list)
    vol_state: Optional[Tuple[float, int, int, int]] = None


def capture_vol_state(doc: Any) -> Optional[Tuple[float, int, int, int]]:
    """
    Capture (volume_mm3, faces_count, edges_count, body_count) of solid bodies
    in the active Part document, or None if not a Part or no solid bodies exist.
    Matches lminventor's _vol_state() for immediate inline agent verification.
    """
    if doc is None:
        return None
    try:
        doc_type = get_attr_or_call(doc, "GetType")
        if doc_type != 1:  # swDocPART = 1
            return None

        bodies = None
        try:
            bodies = doc.GetBodies2(0, False)  # swSolidBody = 0, VisibleOnly = False
        except Exception:
            pass
        if not bodies or len(bodies) == 0:
            return None

        body_count = len(bodies)
        faces_count = 0
        edges_count = 0
        for b in bodies:
            try:
                fc = getattr(b, "GetFaceCount", None)
                if fc is not None:
                    faces_count += int(fc() if callable(fc) else fc)
            except Exception:
                pass
            try:
                ec = getattr(b, "GetEdgeCount", None)
                if ec is not None:
                    edges_count += int(ec() if callable(ec) else ec)
            except Exception:
                pass

        volume_mm3 = 0.0
        mp_resolved = False
        try:
            get_mp = getattr(doc, "GetMassProperties", None)
            mp_arr = get_mp() if callable(get_mp) else get_mp
            if mp_arr and len(mp_arr) >= 4:
                volume_mm3 = float(mp_arr[3]) * 1e9
                mp_resolved = True
        except Exception:
            pass

        if not mp_resolved:
            try:
                ext = getattr(doc, "Extension", None)
                if ext is not None:
                    for mp_name in ("CreateMassProperty2", "CreateMassProperty"):
                        mp_fn = getattr(ext, mp_name, None)
                        if callable(mp_fn):
                            mp = mp_fn()
                            if mp is not None:
                                vol_m3 = get_attr_or_call(mp, "Volume")
                                if vol_m3 is not None:
                                    volume_mm3 = float(vol_m3) * 1e9
                                    mp_resolved = True
                                    break
            except Exception:
                pass

        return (round(volume_mm3, 2), faces_count, edges_count, body_count)
    except Exception as e:
        logger.debug(f"capture_vol_state error: {e}")
        return None


def format_vol_delta(
    before: Optional[Tuple[float, int, int, int]],
    after: Optional[Tuple[float, int, int, int]],
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """
    Format compact ' | V 35271 (-7854 mm³) | F7 E16' suffix and structured geometry_delta dict,
    matching lminventor's _fmt_delta(before) convention.
    """
    if not after:
        return "", None

    vol_after, faces_after, edges_after, bodies_after = after
    body_suffix = f" B{bodies_after}" if bodies_after > 1 else ""

    if not before:
        suffix = f" | V {vol_after:.0f} mm³ | F{faces_after} E{edges_after}{body_suffix}"
        delta_dict = {
            "volume_before_mm3": 0.0,
            "volume_after_mm3": round(vol_after, 2),
            "volume_delta_mm3": round(vol_after, 2),
            "faces_before": 0,
            "faces_after": faces_after,
            "edges_before": 0,
            "edges_after": edges_after,
            "bodies_before": 0,
            "bodies_after": bodies_after,
        }
        return suffix, delta_dict

    vol_before, faces_before, edges_before, bodies_before = before
    dv = vol_after - vol_before
    suffix = f" | V {vol_after:.0f} ({dv:+.0f} mm³) | F{faces_after} E{edges_after}{body_suffix}"
    delta_dict = {
        "volume_before_mm3": round(vol_before, 2),
        "volume_after_mm3": round(vol_after, 2),
        "volume_delta_mm3": round(dv, 2),
        "faces_before": faces_before,
        "faces_after": faces_after,
        "edges_before": edges_before,
        "edges_after": edges_after,
        "bodies_before": bodies_before,
        "bodies_after": bodies_after,
    }
    return suffix, delta_dict


def get_assembly_mate_count(doc: Any) -> int:
    """Count sub-features inside MateGroup in an assembly document."""
    if doc is None:
        return 0
    doc_type = get_attr_or_call(doc, "GetType") if doc else 0
    if doc_type != 2:  # swDocASSEMBLY = 2
        return 0

    count = 0
    try:
        try:
            feat = doc.FirstFeature
        except Exception:
            feat = None
        while feat is not None:
            type_name = get_attr_or_call(feat, "GetTypeName2") or ""
            if type_name == "MateGroup":
                try:
                    sub_feat = feat.GetFirstSubFeature
                except Exception:
                    sub_feat = None
                while sub_feat is not None:
                    count += 1
                    try:
                        sub_feat = sub_feat.GetNextSubFeature
                    except Exception:
                        sub_feat = None
                break
            try:
                feat = feat.GetNextFeature
            except Exception:
                feat = None
    except Exception as e:
        logger.debug(f"get_assembly_mate_count error: {e}")
    return count


def get_model_rebuild_errors(doc: Any) -> List[Dict[str, Any]]:
    """Scan all features in active doc and collect any rebuild errors."""
    if doc is None:
        return []

    errors = []
    is_warn = byref_bool(False)
    try:
        try:
            feat = doc.FirstFeature
        except Exception:
            feat = None

        while feat is not None:
            fname = getattr(feat, "Name", "UnknownFeature")
            ftype = get_attr_or_call(feat, "GetTypeName2")
            try:
                err_code = feat.GetErrorCode2(is_warn)
                if err_code != 0 and not is_warn.value:
                    errors.append({
                        "feature_name": fname,
                        "feature_type": ftype,
                        "error_code": err_code,
                    })
            except Exception as e:
                logger.debug(f"GetErrorCode2 check error for feature '{fname}': {e}")
            try:
                feat = feat.GetNextFeature
            except Exception:
                feat = None
    except Exception as e:
        logger.warning(f"Error scanning features for rebuild errors: {e}")

    return errors


def capture_pre_mutation_state(doc: Any) -> PreMutationState:
    """Capture document feature, component, mate count, and existing rebuild errors before a mutating operation."""
    if doc is None:
        return PreMutationState()

    feat_count = 0
    try:
        feat_mgr = getattr(doc, "FeatureManager", None)
        if feat_mgr:
            feat_count = feat_mgr.GetFeatureCount(True)
    except Exception as e:
        logger.debug(f"Could not get pre-mutation feature count: {e}")

    comp_count = 0
    try:
        get_comp_count = getattr(doc, "GetComponentCount", None)
        if get_comp_count:
            comp_count = get_comp_count(True)
    except Exception as e:
        logger.debug(f"Could not get pre-mutation component count: {e}")

    mate_count = get_assembly_mate_count(doc)
    rebuild_errors = get_model_rebuild_errors(doc)
    vol_state = capture_vol_state(doc)

    return PreMutationState(
        feature_count=feat_count,
        component_count=comp_count,
        mate_count=mate_count,
        rebuild_errors=rebuild_errors,
        vol_state=vol_state,
    )


def verify_mutation_post_condition(
    doc: Any,
    created_obj: Any,
    pre_state: PreMutationState,
    mutation_type: str = "feature",
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """
    Universal post-condition verification:
    1. created_obj is not None (and not False / not an error code)
    2. feature / component / mate count increased
    3. Rebuild errors check: fails ONLY on NEW errors (pre-existing errors are ignored)
    4. For mates: verifies mate's own error code and that mate count increased
    
    Returns:
        (success: bool, message: str, diagnostics: Optional[Dict])
    """
    if doc is None:
        return False, "Active document was closed or invalid during mutation", None

    # Condition 1: Check returned COM object (only for operations that create new entities)
    if mutation_type not in ("general", "dimension", "edit"):
        if created_obj is None or created_obj is False:
            return False, f"COM mutation returned None or failure for {mutation_type}", None

    # Condition 2: Check count increase & type-specific verification
    feat_mgr = getattr(doc, "FeatureManager", None)
    post_feat_count = 0
    if feat_mgr:
        try:
            post_feat_count = feat_mgr.GetFeatureCount(True)
        except Exception:
            pass

    if mutation_type == "component":
        post_comp_count = 0
        try:
            post_comp_count = doc.GetComponentCount(True)
        except Exception:
            pass
        if post_comp_count <= pre_state.component_count:
            return False, f"Component count did not increase (before: {pre_state.component_count}, after: {post_comp_count})", None

    elif mutation_type == "mate":
        # Verify mate COM object
        mate_name = getattr(created_obj, "Name", None)
        if not mate_name:
            return False, "Mate COM object does not have a valid Name", None

        # Verify mate's own feature status / error code
        is_warn = byref_bool(False)
        try:
            get_err = getattr(created_obj, "GetErrorCode2", None)
            if callable(get_err):
                err_code = get_err(is_warn)
                if err_code != 0 and not is_warn.value:
                    return False, f"Mate '{mate_name}' has feature error code {err_code}", {"mate_error": err_code}
        except Exception as ex:
            logger.debug(f"Mate error check exception: {ex}")

        # Verify mate count increased in MateGroup
        post_mate_count = get_assembly_mate_count(doc)
        if post_mate_count <= pre_state.mate_count:
            return False, f"Mate count did not increase (before: {pre_state.mate_count}, after: {post_mate_count})", None

    elif mutation_type == "feature":
        if post_feat_count <= pre_state.feature_count:
            return False, f"Feature count did not increase in feature tree (before: {pre_state.feature_count}, after: {post_feat_count})", None

    # Condition 3: Rebuild error check - fail ONLY on NEW errors
    post_errors = get_model_rebuild_errors(doc)
    pre_error_keys = {(e["feature_name"], e["error_code"]) for e in pre_state.rebuild_errors}
    new_errors = [e for e in post_errors if (e["feature_name"], e["error_code"]) not in pre_error_keys]

    if len(new_errors) > 0:
        err_details = ", ".join(f"{e['feature_name']} (code {e['error_code']})" for e in new_errors)
        return False, f"Mutation introduced {len(new_errors)} new rebuild error(s): {err_details}", {
            "new_errors": new_errors,
            "pre_existing_errors": pre_state.rebuild_errors,
        }

    post_vol_state = capture_vol_state(doc)
    vol_suffix, vol_delta_dict = format_vol_delta(pre_state.vol_state, post_vol_state)

    diag_out: Dict[str, Any] = {
        "post_feature_count": post_feat_count,
        "new_rebuild_errors": 0,
        "pre_existing_rebuild_errors": len(pre_state.rebuild_errors),
    }
    if vol_delta_dict:
        diag_out["geometry_delta"] = vol_delta_dict
        diag_out["volume_suffix"] = vol_suffix

    return True, f"Mutation successfully verified by post-condition check{vol_suffix}", diag_out
