"""
SolidWorks Diagnostics & Recovery Operations
--------------------------------------------
Implements dialog inspection, button clicking, automated prompt dismissal,
dynamic add-in management, and non-blocking rebuild error / relation inspection.
"""

import os
import sys
import logging
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path

from ..constants import SwErrors
from ..core.com_compat import get_attr_or_call, byref_bool
from ..core.com_worker import find_blocking_modal_dialog
from ..utils.dialog_manager import (
    list_active_dialogs,
    dismiss_dialog as win32_dismiss_dialog,
    auto_resolve_blocking_dialogs,
    get_all_solidworks_windows,
)

logger = logging.getLogger("SolidWorksMCP.Diagnostics")

# Known Add-in Name to relative or default path mappings
KNOWN_ADDINS = {
    "simulation": r"Simulation\cosworks.dll",
    "cosmosworks": r"Simulation\cosworks.dll",
    "toolbox": r"Toolbox\SwBrowser.dll",
    "swbrowser": r"Toolbox\SwBrowser.dll",
    "swtoolbox": r"Toolbox\SwBrowser.dll",
    "photoview": r"PhotoView 360\photoview360.dll",
    "routing": r"Routing\routing.dll",
    "motion": r"Motion\motion.dll",
}

# SolidWorks swLoadAddinError_e mappings
SW_LOAD_ADDIN_STATUS = {
    0: ("Add-in loaded successfully", True),
    2: ("Add-in is already loaded and active", True),
    1: ("Add-in failed to load (swAddinNotLoaded)", False),
    3: ("Add-in DLL file was not found by SolidWorks", False),
    4: ("Add-ins are disabled in SolidWorks options", False),
    5: ("Add-in load conflict with another running add-in", False),
    6: ("Add-in COM registration error", False),
    7: ("Add-in license error: license is not available or expired", False),
}


class DiagnosticOperations:
    """Diagnostic, dialog management, and add-in recovery operations mixin."""

    def inspect_dialogs(self) -> Dict[str, Any]:
        """
        Scan and return all active modal popups, error windows, and prompts
        currently blocking SolidWorks.
        """
        try:
            dialogs = list_active_dialogs()
            if not dialogs:
                blocking = find_blocking_modal_dialog()
                if blocking:
                    dialogs = [blocking]
            return {
                "success": True,
                "message": f"Found {len(dialogs)} active SolidWorks dialog(s)",
                "error_code": 0,
                "error_name": "swSuccess",
                "data": {
                    "count": len(dialogs),
                    "dialogs": dialogs,
                }
            }
        except Exception as e:
            logger.error(f"Error inspecting dialogs: {e}")
            return {
                "success": False,
                "message": f"Failed to inspect dialogs: {e}",
                "error_code": SwErrors.swGeneralError,
                "error_name": "swGeneralError",
                "data": {"count": 0, "dialogs": []}
            }

    def dismiss_dialog(
        self,
        hwnd: Optional[int] = None,
        action: str = "OK",
        force_close: bool = False
    ) -> Dict[str, Any]:
        """
        Dismiss a SolidWorks dialog window by clicking a button or sending close signal.

        Args:
            hwnd: Window handle. If None, targets the first active popup dialog.
            action: Button text to click ('OK', 'Cancel', 'Yes', 'No', 'Close', 'Rebuild')
            force_close: Send WM_CLOSE directly
        """
        try:
            blocking = find_blocking_modal_dialog()
            if not blocking:
                return {
                    "success": False,
                    "message": "No active blocking modal dialog detected in SolidWorks to dismiss. Action refused.",
                    "error_code": SwErrors.swInvalidInput,
                    "error_name": "swInvalidInput",
                    "data": {}
                }

            if hwnd is not None and hwnd != blocking["hwnd"]:
                return {
                    "success": False,
                    "message": f"HWND {hwnd} is not the currently detected blocking modal dialog (active modal HWND: {blocking['hwnd']}, title: '{blocking.get('title')}'). Action refused.",
                    "error_code": SwErrors.swInvalidInput,
                    "error_name": "swInvalidInput",
                    "data": {"requested_hwnd": hwnd, "blocking_hwnd": blocking["hwnd"], "title": blocking.get("title")}
                }

            target_hwnd = blocking["hwnd"]
            res = win32_dismiss_dialog(target_hwnd, action=action, force_close=force_close)
            return {
                "success": res.get("success", False),
                "message": res.get("message", "Dialog dismissed"),
                "error_code": 0 if res.get("success") else SwErrors.swInvalidInput,
                "error_name": "swSuccess" if res.get("success") else "swInvalidInput",
                "data": res
            }
        except Exception as e:
            logger.error(f"Error dismissing dialog: {e}")
            return {
                "success": False,
                "message": f"Failed to dismiss dialog: {e}",
                "error_code": SwErrors.swGeneralError,
                "error_name": "swGeneralError",
                "data": {}
            }

    def auto_resolve_dialogs(self) -> Dict[str, Any]:
        """Auto-resolve all known safe non-destructive blocking dialogs."""
        try:
            results = auto_resolve_blocking_dialogs()
            return {
                "success": True,
                "message": f"Auto-resolved {len(results)} dialog(s)",
                "error_code": 0,
                "error_name": "swSuccess",
                "data": {"resolved_count": len(results), "resolved": results}
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"Error during auto-resolve: {e}",
                "error_code": SwErrors.swGeneralError,
                "error_name": "swGeneralError",
                "data": {}
            }

    def load_addin(self, name_or_path: str) -> Dict[str, Any]:
        """
        Programmatically load and enable a SolidWorks add-in DLL at runtime.

        Args:
            name_or_path: Add-in nickname ('simulation', 'toolbox', 'routing', 'motion', 'photoview')
                          or absolute path to .dll
        """
        def _worker():
            if not self.app:
                return False, "Not connected to SolidWorks", None

            dll_path = name_or_path
            norm_name = name_or_path.strip().lower()

            if norm_name in KNOWN_ADDINS:
                # Find SolidWorks install root dynamically
                from ..utils import resolve_solidworks_install_dir
                install_dir = resolve_solidworks_install_dir(self.app)
                candidate = install_dir / KNOWN_ADDINS[norm_name]
                if candidate.exists():
                    dll_path = str(candidate)
                else:
                    # Try other common drive installations
                    found_cand = None
                    for alt_root in [
                        r"C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS",
                        r"C:\Program Files\SolidWorks Corp\SolidWorks",
                        r"D:\Program Files\SOLIDWORKS Corp\SOLIDWORKS",
                        r"E:\Program Files\SOLIDWORKS Corp\SOLIDWORKS",
                    ]:
                        def_candidate = Path(alt_root) / KNOWN_ADDINS[norm_name]
                        if def_candidate.exists():
                            found_cand = str(def_candidate)
                            break
                    if found_cand:
                        dll_path = found_cand
                    else:
                        return False, f"Could not find add-in DLL for '{name_or_path}' at {candidate}", None

            if not os.path.exists(dll_path):
                return False, f"Add-in DLL path does not exist: {dll_path}", None

            # Fast check: if add-in is already loaded and active, return success immediately
            already_loaded = False
            if norm_name in ("simulation", "cosmosworks", "cosmos"):
                try:
                    sim_obj = self.app.GetAddInObject("CosmosWorks.CosmosWorks") or self.app.GetAddInObject("SldWorks.Simulation")
                    already_loaded = sim_obj is not None
                except Exception:
                    already_loaded = False
            elif norm_name in ("toolbox", "swbrowser"):
                try:
                    tb_obj = self.app.GetAddInObject("swBrowser.Browser")
                    already_loaded = tb_obj is not None
                except Exception:
                    already_loaded = False

            if already_loaded:
                return True, f"Add-in '{name_or_path}' is already loaded and active.", {"dll_path": dll_path, "return_code": 0, "verified_loaded": True, "already_loaded": True}

            logger.info(f"Loading SolidWorks Add-In: {dll_path}")
            try:
                ret_code = self.app.LoadAddIn(dll_path)
                if ret_code in SW_LOAD_ADDIN_STATUS:
                    desc, is_success = SW_LOAD_ADDIN_STATUS[ret_code]
                    if is_success:
                        # Post-condition check: verify add-in is actually loaded in SolidWorks
                        verified = True
                        if norm_name in ("simulation", "cosmosworks", "cosmos"):
                            try:
                                sim_obj = self.app.GetAddInObject("CosmosWorks.CosmosWorks") or self.app.GetAddInObject("SldWorks.Simulation")
                                verified = sim_obj is not None
                            except Exception:
                                verified = False
                        elif norm_name in ("toolbox", "swbrowser"):
                            try:
                                tb_obj = self.app.GetAddInObject("swBrowser.Browser")
                                verified = tb_obj is not None
                            except Exception:
                                verified = False

                        if not verified:
                            msg = f"Post-condition failed: Add-in '{name_or_path}' returned code {ret_code} but COM add-in object could not be resolved."
                            return False, msg, {"dll_path": dll_path, "return_code": ret_code, "verified_loaded": False}

                        msg = f"Add-in '{name_or_path}' loaded successfully (code {ret_code}: {desc})."
                        return True, msg, {"dll_path": dll_path, "return_code": ret_code, "verified_loaded": True}
                    elif ret_code == 7:
                        msg = f"License unavailable: add-in '{name_or_path}' is not licensed in this SolidWorks installation (code 7: {desc})."
                        return False, msg, {"dll_path": dll_path, "return_code": ret_code}
                    else:
                        msg = f"Could not load add-in '{name_or_path}': {desc} (code {ret_code})."
                        return False, msg, {"dll_path": dll_path, "return_code": ret_code}
                else:
                    success = (ret_code == 0)
                    msg = f"Add-in loaded (code {ret_code})" if success else f"LoadAddIn returned code {ret_code}"
                    return success, msg, {"dll_path": dll_path, "return_code": ret_code}
            except Exception as e:
                return False, f"License or COM error while loading add-in '{name_or_path}': {e}", {"dll_path": dll_path, "error": str(e)}

        try:
            res = _worker()
        except Exception as e:
            logger.error(f"Exception while loading add-in: {e}\n{traceback.format_exc()}")
            res = (False, f"Exception while loading add-in: {e}", None)

        if isinstance(res, tuple):
            ok, msg, data = res
            if ok:
                err_code = SwErrors.swSuccess
            elif "not connected" in msg.lower():
                err_code = SwErrors.swConnectionError
            elif "not found" in msg.lower():
                err_code = SwErrors.swFileNotFoundError
            else:
                err_code = SwErrors.swGeneralError
            return self._result(ok, msg, err_code, data)
        return res

    def inspect_rebuild_errors(self) -> Dict[str, Any]:
        """
        Inspect active document for feature rebuild errors, broken sketch relations,
        dangling assembly mates, and overdefined geometry without opening modal dialogs.
        """
        def _worker():
            doc, err = self.get_active_doc()
            if err:
                return err

            dtype = self.get_document_type(doc)
            if dtype == "Drawing":
                return self._result(
                    False,
                    "inspect_rebuild_errors is not supported for Drawing documents",
                    SwErrors.swInvalidDocumentType
                )

            errors_list = []
            warnings_list = []

            is_warn = byref_bool(False)

            # 1. Traversal of features to check error codes
            try:
                feat = doc.FirstFeature
                while feat:
                    fname = getattr(feat, "Name", "UnknownFeature")
                    ftype = get_attr_or_call(feat, "GetTypeName2")
                    err_code = 0
                    try:
                        err_code = feat.GetErrorCode2(is_warn)
                    except Exception as e:
                        logger.debug(f"Failed to call GetErrorCode2 on feature '{fname}': {e}")

                    if err_code != 0:
                        desc = f"Feature '{fname}' has {'warning' if is_warn.value else 'error'} code {err_code}"
                        entry = {
                            "feature_name": fname,
                            "feature_type": ftype,
                            "error_code": err_code,
                            "is_warning": bool(is_warn.value),
                            "description": desc,
                        }
                        if is_warn.value:
                            warnings_list.append(entry)
                        else:
                            errors_list.append(entry)

                    # Check sketch constraint status if it's a sketch
                    if "sketch" in str(ftype).lower() or ftype == "ProfileFeature":
                        try:
                            sub_feat = get_attr_or_call(feat, "GetSpecificFeature2")
                            if sub_feat:
                                status = getattr(sub_feat, "ConstrainedStatus", None)
                                # 0: underconstrained, 1: fully constrained, 2: overconstrained, 3: invalid/dangling
                                status_map = {
                                    0: "UnderDefined",
                                    1: "FullyDefined",
                                    2: "OverDefined",
                                    3: "DanglingOrInvalid"
                                }
                                if status in [2, 3]:
                                    warnings_list.append({
                                        "feature_name": fname,
                                        "feature_type": ftype,
                                        "status": status_map.get(status, str(status)),
                                        "description": f"Sketch has broken/conflicting relations ({status_map.get(status)})"
                                    })
                        except Exception as e:
                            logger.debug(f"Failed to check sketch constraint status on '{fname}': {e}")

                    # Check assembly mates if it's a MateGroup
                    if ftype == "MateGroup":
                        try:
                            mate = get_attr_or_call(feat, "GetFirstSubFeature")
                            while mate:
                                m_err = 0
                                m_warn = byref_bool(False)
                                try:
                                    m_err = mate.GetErrorCode2(m_warn)
                                except Exception as e:
                                    logger.debug(f"Failed to check mate error on '{getattr(mate, 'Name', '')}': {e}")
                                if m_err != 0:
                                    entry = {
                                        "feature_name": getattr(mate, "Name", "Mate"),
                                        "feature_type": "AssemblyMate",
                                        "error_code": m_err,
                                        "is_warning": bool(m_warn.value),
                                        "description": f"Mate reference broken or overdefined (code {m_err})"
                                    }
                                    if m_warn.value:
                                        warnings_list.append(entry)
                                    else:
                                        errors_list.append(entry)
                                mate = get_attr_or_call(mate, "GetNextSubFeature")
                        except Exception as e:
                            logger.debug(f"Failed to traverse MateGroup subfeatures: {e}")

                    feat = feat.GetNextFeature
            except Exception as ex:
                logger.error(f"Error traversing feature errors: {ex}\n{traceback.format_exc()}")
                return False, f"Failed to traverse feature errors: {ex}", None

            # Rebuild clean status
            rebuild_clean = (len(errors_list) == 0 and len(warnings_list) == 0)
            return True, f"Rebuild status: {'CLEAN' if rebuild_clean else 'ERRORS_DETECTED'}", {
                "rebuild_clean": rebuild_clean,
                "error_count": len(errors_list),
                "warning_count": len(warnings_list),
                "errors": errors_list,
                "warnings": warnings_list,
            }

        try:
            res = _worker()
        except Exception as e:
            logger.error(f"inspect_rebuild_errors failed: {e}\n{traceback.format_exc()}")
            res = (False, f"inspect_rebuild_errors failed: {e}", None)

        if isinstance(res, tuple):
            ok, msg, data = res
            return self._result(ok, msg, SwErrors.swSuccess if ok else SwErrors.swGeneralError, data)
        return res
