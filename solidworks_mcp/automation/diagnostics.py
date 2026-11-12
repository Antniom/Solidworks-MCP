"""
SolidWorks Diagnostics & Recovery Operations
--------------------------------------------
Implements dialog inspection, button clicking, automated prompt dismissal,
dynamic add-in management, and non-blocking rebuild error / relation inspection.
"""

import os
import time
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

    def supervisor_clean_processes(
        self,
        confirm: bool = False,
        target_pids: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        """
        Audit all running CAD processes, distinguishing active visible user windows
        from orphaned headless/background processes (not in ROT or MainWindowTitle == '').
        If confirm=False (default), performs a dry-run audit.
        If confirm=True, terminates orphaned zombies (or specific target_pids).
        """
        try:
            import pythoncom
            import win32api
            import win32con
            import win32process
            from .base import find_sldworks_pids, get_process_details

            pids = find_sldworks_pids()
            if not pids:
                return self._result(
                    True,
                    "No running SolidWorks processes found.",
                    SwErrors.swSuccess,
                    {"processes": [], "zombies": [], "terminated_pids": [], "confirm": confirm},
                )

            # Discover which PIDs are registered in the Running Object Table (ROT)
            rot_pids = set()
            try:
                pythoncom.CoInitialize()
                rot = pythoncom.GetRunningObjectTable()
                enum = rot.EnumRunning()
                ctx = pythoncom.CreateBindCtx()
                for m in enum:
                    try:
                        disp_name = m.GetDisplayName(ctx, None).lower()
                        if "solidworks_pid_" in disp_name:
                            tail = disp_name.split("solidworks_pid_")[-1]
                            num_str = "".join(ch for ch in tail if ch.isdigit())
                            if num_str:
                                rot_pids.add(int(num_str))
                    except Exception:
                        pass
            except Exception:
                pass

            connected_pid = getattr(self, "_connected_pid", None)
            processes_info = []
            zombie_pids = []

            for pid in pids:
                info = get_process_details(pid)
                in_rot = pid in rot_pids
                has_title = bool(str(info.get("window_title") or "").strip())
                is_active_user = has_title or (in_rot and info.get("working_set_mb", 0.0) > 150.0) or (pid == connected_pid and has_title)
                classification = "active_visible" if is_active_user else "orphaned_zombie"
                entry = {
                    **info,
                    "in_rot": in_rot,
                    "classification": classification,
                }
                processes_info.append(entry)
                if classification == "orphaned_zombie":
                    zombie_pids.append(pid)

            to_kill = []
            if target_pids is not None:
                req_set = {int(p) for p in target_pids}
                to_kill = [p for p in pids if p in req_set]
            else:
                to_kill = list(zombie_pids)

            terminated = []
            errors = []
            if confirm and to_kill:
                for kpid in to_kill:
                    try:
                        h = win32api.OpenProcess(win32con.PROCESS_TERMINATE, False, int(kpid))
                        win32process.TerminateProcess(h, 1)
                        win32api.CloseHandle(h)
                        terminated.append(int(kpid))
                        if getattr(self, "_connected_pid", None) == int(kpid):
                            self._connected = False
                            self._sw_app = None
                            self._connected_pid = None
                    except Exception as kex:
                        errors.append({"pid": int(kpid), "error": str(kex)})

            if not confirm:
                msg = (
                    f"Dry-run process audit: {len(pids)} SolidWorks process(es) found, "
                    f"{len(zombie_pids)} orphaned/headless zombie(s) ({zombie_pids}). "
                    f"Pass confirm=True to terminate {to_kill}."
                )
            else:
                msg = (
                    f"Terminated {len(terminated)} orphaned process(es): {terminated} "
                    f"(remaining active: {[p for p in pids if p not in terminated]})."
                )

            return self._result(
                True,
                msg,
                SwErrors.swSuccess,
                {
                    "processes": processes_info,
                    "zombies": zombie_pids,
                    "target_kill_pids": to_kill,
                    "terminated_pids": terminated,
                    "errors": errors,
                    "confirm": confirm,
                },
            )
        except Exception as e:
            logger.error(f"supervisor_clean_processes error: {e}")
            return self._result(False, f"Process supervisor error: {e}", SwErrors.swGeneralError)

    def transaction(self, action: str = "begin", name: str = "MCP batch") -> Dict[str, Any]:
        """
        Manage an Undo transaction wrapping multi-step CAD operations.
        action: 'begin' | 'commit' | 'abort'
        """
        try:
            from ..core import get_checkpoint_manager, capture_vol_state, format_vol_delta

            act = (action or "begin").strip().lower()
            doc, err = self.get_active_doc()
            if err:
                return err

            active_txn = getattr(self, "_active_txn", None)

            if act == "begin":
                if active_txn is not None:
                    return self._result(
                        False,
                        f"Transaction '{active_txn.get('name')}' is already open — commit or abort it first.",
                        SwErrors.swInvalidCall,
                        {"active_transaction": active_txn},
                    )
                vol_before = capture_vol_state(doc)
                ckpt_name = f"txn_{name}_{time.time_ns()}"
                ckpt_res = get_checkpoint_manager().create_checkpoint(
                    doc=doc,
                    name=ckpt_name,
                    description=f"Undo transaction: {name}",
                    snapshot=False,
                )
                undo_started = False
                try:
                    start_undo = getattr(doc, "StartUndoRecord", None)
                    if callable(start_undo):
                        start_undo()
                        undo_started = True
                    elif start_undo is not None:
                        undo_started = True
                except Exception:
                    pass

                self._active_txn = {
                    "name": name,
                    "checkpoint_name": ckpt_name if ckpt_res.get("success") else None,
                    "undo_record_active": undo_started,
                    "vol_before": vol_before,
                }
                return self._result(
                    True,
                    f"Transaction '{name}' started",
                    SwErrors.swSuccess,
                    self._active_txn,
                )

            elif act == "commit":
                if active_txn is None:
                    return self._result(False, "No open transaction to commit.", SwErrors.swInvalidCall)
                try:
                    end_undo = getattr(doc, "EndUndoRecord", None)
                    if callable(end_undo):
                        end_undo()
                except Exception:
                    pass
                vol_after = capture_vol_state(doc)
                suffix, delta_dict = format_vol_delta(active_txn.get("vol_before"), vol_after)
                txn_name = active_txn.get("name", name)
                self._active_txn = None
                return self._result(
                    True,
                    f"Transaction '{txn_name}' committed{suffix}",
                    SwErrors.swSuccess,
                    {"transaction": txn_name, "status": "committed", "geometry_delta": delta_dict},
                )

            elif act == "abort":
                if active_txn is None:
                    return self._result(False, "No open transaction to abort.", SwErrors.swInvalidCall)
                try:
                    end_undo = getattr(doc, "EndUndoRecord", None)
                    if callable(end_undo):
                        end_undo()
                except Exception:
                    pass

                txn_name = active_txn.get("name", name)
                ckpt_name = active_txn.get("checkpoint_name")
                vol_start = active_txn.get("vol_before")
                deleted: List[str] = []
                undo_used = False

                # 1. Remove features created since 'begin'.
                if ckpt_name:
                    rb_res = get_checkpoint_manager().rollback_checkpoint(doc=doc, name=ckpt_name)
                    if rb_res.get("success"):
                        deleted = rb_res.get("deleted_features", [])

                # 2. Anything else (dimension edits, suppression, renames) is only reachable
                #    through the undo record opened at 'begin'.
                #    It is used only when geometry is measurably off, so an empty transaction
                #    never undoes the user's earlier, unrelated work.
                if capture_vol_state(doc) != vol_start:
                    if active_txn.get("undo_record_active"):
                        try:
                            undo_fn = getattr(doc, "EditUndo2", None)
                            if callable(undo_fn):
                                undo_fn(1)
                                undo_used = True
                        except Exception:
                            pass

                self._active_txn = None
                geometry_restored = capture_vol_state(doc) == vol_start
                data = {
                    "transaction": txn_name,
                    "status": "aborted",
                    "rolled_back": geometry_restored,
                    "deleted_features": deleted,
                    "undo_used": undo_used,
                }
                if geometry_restored:
                    return self._result(
                        True,
                        f"Transaction '{txn_name}' aborted - geometry is back to its state at 'begin' "
                        f"({len(deleted)} feature(s) removed). Non-geometric edits such as renames are not verified.",
                        SwErrors.swSuccess,
                        data,
                    )
                return self._result(
                    False,
                    f"Transaction '{txn_name}' closed, but the model could NOT be fully restored: geometry still "
                    f"differs from its state at 'begin'. Inspect the model (list_features, get_dimensions) and "
                    f"revert the remaining changes manually.",
                    SwErrors.swGeneralError,
                    data,
                )

            else:
                return self._result(
                    False,
                    f"Unknown transaction action '{action}'. Use 'begin', 'commit', or 'abort'.",
                    SwErrors.swInvalidParameter,
                )
        except Exception as e:
            logger.error(f"transaction error: {e}")
            return self._result(False, f"Transaction error: {e}", SwErrors.swGeneralError)
