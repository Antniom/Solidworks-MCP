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

    return PreMutationState(
        feature_count=feat_count,
        component_count=comp_count,
        mate_count=mate_count,
        rebuild_errors=rebuild_errors,
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

    return True, "Mutation successfully verified by post-condition check", {
        "post_feature_count": post_feat_count,
        "new_rebuild_errors": 0,
        "pre_existing_rebuild_errors": len(pre_state.rebuild_errors),
    }
