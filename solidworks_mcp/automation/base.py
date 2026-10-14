"""
SolidWorks Automation Base
--------------------------
Core automation class with connection management and utility methods.
"""

import os
import subprocess
import time
import logging
import datetime
import traceback
import json
from typing import Optional, Dict, Any, Tuple

# COM imports
try:
    import win32com.client
    import pythoncom
    import win32api
    import win32con
    import win32process
    import win32security
    import win32gui
    import ctypes
    HAS_PYWIN32 = True
except ImportError:
    HAS_PYWIN32 = False

from ..constants import SwErrors, SwPlanes, SwDocumentTypes, SwViews
from ..config import get_config
from ..utils import UnitConverter, find_solidworks, find_template
from ..core import get_sta_worker, SolidWorksModalBlockedException
from ..core.com_compat import get_attr_or_call
from ..utils.dialog_manager import find_blocking_modal_dialog

logger = logging.getLogger(__name__)


def is_rpc_disconnect_error(ex: Exception) -> bool:
    """Check if exception is a COM RPC disconnect, wrong thread apartment, or server unavailable error."""
    hresult = getattr(ex, "hresult", None)
    if hresult is None and hasattr(ex, "args") and len(ex.args) > 0 and isinstance(ex.args[0], int):
        hresult = ex.args[0]
    if hresult in (-2147023170, -2147417848, 0x800706BA, 0x80010108, -2147417842, 0x8001010E):
        return True
    msg = str(ex).lower()
    return (
        "0x800706ba" in msg
        or "rpc server is unavailable" in msg
        or "the remote procedure call failed" in msg
        or "marshalled for a different thread" in msg
        or "different thread" in msg
        or "0x8001010e" in msg
    )


def find_sldworks_pids() -> list[int]:
    """Find all running SLDWORKS.exe process IDs."""
    sw_pids = []
    try:
        pids = win32process.EnumProcesses()
        for pid in pids:
            try:
                h = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
                name = win32process.GetModuleFileNameEx(h, 0)
                win32api.CloseHandle(h)
                if "sldworks.exe" in name.lower():
                    sw_pids.append(pid)
            except Exception:
                pass
    except Exception as ex:
        logger.debug(f"find_sldworks_pids error: {ex}")
    return sw_pids


def check_uac_elevation_mismatch() -> Optional[str]:
    """
    Check if SolidWorks is running at a different UAC elevation level than the current process.
    Returns a diagnostic message if a mismatch is detected, None otherwise.
    """
    try:
        try:
            current_token = win32security.OpenProcessToken(win32process.GetCurrentProcess(), win32security.TOKEN_QUERY)
            current_elevated = bool(win32security.GetTokenInformation(current_token, 20))
        except Exception:
            current_elevated = bool(ctypes.windll.shell32.IsUserAnAdmin())
            
        sw_pids = find_sldworks_pids()
        if not sw_pids:
            return None

        for pid in sw_pids:
            try:
                h = win32api.OpenProcess(win32con.PROCESS_QUERY_INFORMATION, False, pid)
                token = win32security.OpenProcessToken(h, win32security.TOKEN_QUERY)
                sw_elevated = bool(win32security.GetTokenInformation(token, 20))
                win32api.CloseHandle(h)

                if sw_elevated != current_elevated:
                    if sw_elevated and not current_elevated:
                        return (
                            f"UAC elevation mismatch: SolidWorks (PID {pid}) is running as Administrator (elevated), "
                            f"while SolidWorks MCP is running as a standard non-elevated user. "
                            f"Windows COM cannot communicate across integrity levels (UIPI). "
                            f"Please restart both applications at the same elevation level."
                        )
                    else:
                        return (
                            f"UAC elevation mismatch: SolidWorks MCP is running as Administrator (elevated), "
                            f"while SolidWorks (PID {pid}) is running as a standard non-elevated user. "
                            f"Windows COM cannot communicate across integrity levels (UIPI). "
                            f"Please restart both applications at the same elevation level."
                        )
            except Exception as ex:
                if getattr(ex, "winerror", None) == 5 or "access is denied" in str(ex).lower():
                    if not current_elevated:
                        return (
                            f"UAC elevation mismatch: SolidWorks (PID {pid}) is running with higher (elevated) privileges, "
                            f"blocking standard COM access. Please run SolidWorks MCP as Administrator."
                        )
    except Exception as ex:
        logger.debug(f"check_uac_elevation_mismatch exception: {ex}")
    return None


def get_process_details(pid: int) -> Dict[str, Any]:
    """Get diagnostic details (PID, StartTime, WorkingSetMB, MainWindowTitle, ParentPID, ParentName, SessionId) for an SLDWORKS.exe process."""
    details = {
        "pid": pid,
        "start_time": "Unknown",
        "working_set_mb": 0.0,
        "window_title": "",
        "parent_pid": None,
        "parent_name": "Unknown",
        "session_id": None,
        "is_com_launched": False,
    }
    try:
        ps_cmd = f"""
        $p = Get-Process -Id {pid} -ErrorAction SilentlyContinue
        if ($p) {{
            $wsMB = [math]::Round($p.WorkingSet64 / 1MB, 2)
            $st = try {{ $p.StartTime.ToString('yyyy-MM-dd HH:mm:ss') }} catch {{ 'Unknown' }}
            $ppid = (Get-CimInstance Win32_Process -Filter "ProcessId = {pid}").ParentProcessId
            $pname = if ($ppid) {{ (Get-Process -Id $ppid -ErrorAction SilentlyContinue).ProcessName }} else {{ 'Unknown' }}
            [PSCustomObject]@{{
                ProcessId = $p.Id
                StartTime = $st
                WorkingSetMB = $wsMB
                MainWindowTitle = $p.MainWindowTitle
                ParentPID = $ppid
                ParentName = $pname
                SessionId = $p.SessionId
            }} | ConvertTo-Json -Compress
        }}
        """
        res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, text=True, timeout=5)
        if res.stdout.strip():
            d = json.loads(res.stdout.strip())
            details["start_time"] = d.get("StartTime", "Unknown")
            details["working_set_mb"] = d.get("WorkingSetMB", 0.0)
            details["window_title"] = d.get("MainWindowTitle", "")
            details["parent_pid"] = d.get("ParentPID")
            parent_name = str(d.get("ParentName") or "Unknown")
            details["parent_name"] = parent_name
            details["session_id"] = d.get("SessionId")
            details["is_com_launched"] = (
                "svchost" in parent_name.lower()
                or "dcom" in parent_name.lower()
            )
    except Exception as ex:
        logger.debug(f"get_process_details error for PID {pid}: {ex}")
    return details


def get_running_app() -> Optional[Any]:
    """
    Get the active running SolidWorks application instance via ROT/GetActiveObject only.
    Guaranteed NEVER to launch SLDWORKS.exe or create a new COM instance.
    """
    if not HAS_PYWIN32:
        return None
    try:
        app = win32com.client.GetActiveObject("SldWorks.Application")
        if app is not None:
            return app
    except Exception:
        pass

    # Check ROT for SolidWorks_PID_ moniker
    try:
        rot = pythoncom.GetRunningObjectTable()
        enum = rot.EnumRunning()
        ctx = pythoncom.CreateBindCtx()
        for m in enum:
            try:
                name = m.GetDisplayName(ctx, None).lower()
                if "solidworks_pid_" in name:
                    unk = rot.GetObject(m)
                    disp = unk.QueryInterface(pythoncom.IID_IDispatch)
                    app = win32com.client.Dispatch(disp)
                    if app is not None:
                        return app
            except Exception:
                pass
    except Exception:
        pass
    return None


def launch_solidworks(
    exe_path: Optional[str] = None,
    startup_timeout: float = 60.0,
    retry_interval: float = 2.0
) -> Tuple[Optional[Any], Optional[str]]:
    """
    Sole authorized function in the entire codebase permitted to launch SLDWORKS.exe
    and establish the initial COM connection.
    Guarantees that EVERY launch is visible, has UserControl=True, and is confirmed
    to be registered in the Running Object Table (ROT) before returning.
    """
    if not HAS_PYWIN32:
        return None, "pywin32 is not installed"

    # Strict precondition: ensure no SLDWORKS.exe is already running
    pids = find_sldworks_pids()
    if pids:
        return None, f"Refusing to launch: SLDWORKS.exe process(es) already exist (PID(s): {pids})"

    target_exe = exe_path or find_solidworks()
    used_dispatch_fallback = False
    if not target_exe or not os.path.exists(target_exe):
        logger.info("SolidWorks executable not found on disk; checking running processes before COM Dispatch fallback")
        pids = find_sldworks_pids()
        if pids:
            return None, f"Refusing COM Dispatch fallback: SLDWORKS.exe process(es) already exist (PID(s): {pids})"
        try:
            app = win32com.client.Dispatch("SldWorks.Application")
            try:
                app.Visible = True
                app.UserControl = True
            except Exception:
                pass
            used_dispatch_fallback = True
        except Exception as e:
            return None, f"COM Dispatch fallback failed: {e}"
    else:
        logger.info(f"Launching SolidWorks desktop process: {target_exe}")
        try:
            si = subprocess.STARTUPINFO()
            si.lpDesktop = r"WinSta0\Default"
            subprocess.Popen([target_exe], startupinfo=si)
        except Exception as launch_err:
            logger.warning(f"Failed to launch on WinSta0\\Default ({launch_err}), falling back to os.startfile")
            try:
                os.startfile(target_exe)
            except Exception as e:
                logger.warning(f"os.startfile failed ({e}), checking running processes before COM Dispatch fallback")
                pids = find_sldworks_pids()
                if pids:
                    return None, f"Refusing COM Dispatch fallback: SLDWORKS.exe process(es) already exist (PID(s): {pids})"
                try:
                    app = win32com.client.Dispatch("SldWorks.Application")
                    try:
                        app.Visible = True
                        app.UserControl = True
                    except Exception:
                        pass
                    used_dispatch_fallback = True
                except Exception as disp_err:
                    return None, f"Failed to start SolidWorks: {launch_err}; Dispatch fallback failed: {disp_err}"

    # Poll until the instance is confirmed registered in the Running Object Table (ROT)
    start_time = time.time()
    confirmed_app = None
    while time.time() - start_time < startup_timeout:
        time.sleep(retry_interval)
        confirmed_app = get_running_app()
        if confirmed_app is not None:
            try:
                confirmed_app.Visible = True
                confirmed_app.UserControl = True
            except Exception:
                pass
            # Directly verify that GetActiveObject from another caller can attach via ROT
            try:
                rot_check = win32com.client.GetActiveObject("SldWorks.Application")
                if rot_check is not None:
                    logger.info("SolidWorks launch verified: registered in ROT and attachable via GetActiveObject.")
                    return confirmed_app, None
            except Exception:
                # SolidWorks is still finalizing ROT moniker publishing
                pass

    if confirmed_app is not None:
        try:
            confirmed_app.Visible = True
            confirmed_app.UserControl = True
        except Exception:
            pass
        return confirmed_app, None

    return None, f"Startup timed out after {startup_timeout}s: SolidWorks did not register in Running Object Table (ROT)"


def ensure_visible_and_foreground(sw_app) -> bool:
    """
    Guarantees that SolidWorks is visible, unminimized, and brought to
    the foreground of the user's desktop.
    """
    if sw_app is None:
        return False
    try:
        sw_app.Visible = True
        sw_app.UserControl = True
    except Exception:
        pass

    try:
        hwnd = 0
        try:
            frame = getattr(sw_app, "Frame", None)
            if frame:
                hwnd = getattr(frame, "GetHWndx64", lambda: 0)()
                if not hwnd:
                    hwnd = getattr(frame, "GetHWnd", lambda: 0)()
        except Exception:
            pass

        if not hwnd:
            try:
                pid = get_attr_or_call(sw_app, "GetProcessID")
                if pid:
                    def enum_cb(h, found):
                        if win32gui.IsWindowVisible(h) or win32gui.IsIconic(h):
                            _, w_pid = win32process.GetWindowThreadProcessId(h)
                            if w_pid == pid:
                                title = win32gui.GetWindowText(h)
                                if "solidworks" in title.lower():
                                    found.insert(0, h)
                                elif not found:
                                    found.append(h)
                        return True
                    found = []
                    win32gui.EnumWindows(enum_cb, found)
                    if found:
                        hwnd = found[0]
            except Exception:
                pass

        if hwnd:
            try:
                if win32gui.IsIconic(hwnd):
                    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                else:
                    win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
                win32gui.ShowWindow(hwnd, win32con.SW_SHOWMAXIMIZED)

                cur_thread = win32api.GetCurrentThreadId()
                target_thread, _ = win32process.GetWindowThreadProcessId(hwnd)
                if cur_thread != target_thread:
                    ctypes.windll.user32.AttachThreadInput(cur_thread, target_thread, True)
                    ctypes.windll.user32.SetForegroundWindow(hwnd)
                    ctypes.windll.user32.SetFocus(hwnd)
                    ctypes.windll.user32.AttachThreadInput(cur_thread, target_thread, False)
                else:
                    win32gui.SetForegroundWindow(hwnd)
                return True
            except Exception as e:
                logger.debug(f"Could not bring window to foreground: {e}")
    except Exception as e:
        logger.debug(f"ensure_visible_and_foreground error: {e}")
    return False


class SolidWorksAutomation:
    """
    Core SolidWorks automation class
    
    Handles connection management, document operations, and provides
    utility methods for all automation tasks.
    """
    
    def __init__(self):
        """Initialize automation instance"""
        self._sw_app = None
        self._connected = False
        self._connected_pid: Optional[int] = None
        self._config = get_config()
        self._units = UnitConverter(self._config.default_unit)
        self._sw_exe_path = None
        self._sta = get_sta_worker()
        self._original_pref_toggle_11: Optional[bool] = None
        
        logger.info("SolidWorksAutomation initialized with STA COM worker")
    
    def run_on_sta(self, func, *args, **kwargs):
        """Execute a callable synchronously on the dedicated STA COM worker thread with auto-reconnect on RPC disconnect."""
        try:
            return get_sta_worker().run_sync(func, *args, **kwargs)
        except SolidWorksModalBlockedException as ex:
            logger.warning(f"STA worker blocked by modal dialog: {ex}")
            return self._result(
                False,
                str(ex),
                SwErrors.swGeneralError,
                {"blocked_by_dialog": True, "dialog": ex.dialog_info}
            )
        except Exception as ex:
            if is_rpc_disconnect_error(ex):
                logger.warning(f"RPC server unavailable ({ex}). Attempting auto-reconnect once...")
                self._connected = False
                self._sw_app = None
                self._connected_pid = None
                reconn = self.connect()
                if reconn.get("success"):
                    logger.info("Auto-reconnect succeeded, retrying operation once...")
                    try:
                        return get_sta_worker().run_sync(func, *args, **kwargs)
                    except Exception as retry_ex:
                        logger.error(f"Operation failed after auto-reconnect: {retry_ex}")
                        return self._result(
                            False,
                            f"Operation failed after auto-reconnect: {retry_ex}",
                            SwErrors.swConnectionError
                        )
                else:
                    return self._result(
                        False,
                        f"SolidWorks RPC disconnected and auto-reconnect failed: {reconn.get('message')}",
                        SwErrors.swConnectionError
                    )
            raise
    
    # ========================================================================
    # Properties
    # ========================================================================
    
    @property
    def is_connected(self) -> bool:
        """Check if connected to SolidWorks without false disconnects on busy states."""
        if not self._connected or self._sw_app is None:
            return False
        
        # Check if underlying process is still alive when PID is known
        pid = getattr(self, "_connected_pid", None)
        if pid:
            try:
                handle = win32api.OpenProcess(win32con.PROCESS_QUERY_INFORMATION, False, pid)
                exit_code = win32process.GetExitCodeProcess(handle)
                win32api.CloseHandle(handle)
                if exit_code != 259:  # STILL_ACTIVE = 259
                    self._connected = False
                    self._sw_app = None
                    self._connected_pid = None
                    return False
            except Exception:
                pass
        
        try:
            _ = get_attr_or_call(self._sw_app, "Visible")
            return True
        except Exception as e:
            # If busy (RPC_E_SERVERCALL_RETRYLATER -2147417846 / 0x8001010A), SolidWorks is STILL connected!
            if hasattr(e, "args") and len(e.args) > 0 and e.args[0] in (-2147417846, 0x8001010A, -2147418111, 0x80010001):
                return True
            # Only disconnect if it's a true RPC disconnect or server unavailable
            if is_rpc_disconnect_error(e):
                self._connected = False
                self._sw_app = None
                self._connected_pid = None
                return False
            # For other unexpected COM errors, do NOT falsely disconnect
            return self._connected
    
    @property
    def active_doc(self):
        """Get active SolidWorks document if connected, else None."""
        if not self.is_connected or self._sw_app is None:
            return None
        try:
            return self._sw_app.ActiveDoc
        except Exception:
            return None

    @property
    def units(self) -> UnitConverter:
        """Get unit converter"""
        return self._units

    def set_units(self, unit: str = "mm") -> Dict[str, Any]:
        """Set default unit for operations and active document."""
        try:
            if not self.is_connected and not getattr(self._config, "auto_connect", True):
                return self._result(
                    False,
                    "Not connected to SolidWorks. Call connect_solidworks first.",
                    SwErrors.swConnectionError
                )

            from ..utils import set_default_unit
            from ..utils.units import UNIT_ALIASES

            clean_unit = str(unit).strip().lower()
            if clean_unit not in UNIT_ALIASES:
                return self._result(
                    False,
                    f"Invalid unit string '{unit}'. Allowed: 'mm', 'inch', 'm', 'cm', 'ft'.",
                    SwErrors.swInvalidInput
                )
            resolved_unit = UNIT_ALIASES[clean_unit].value
            set_default_unit(resolved_unit)
            if hasattr(self, "_units"):
                self._units.default_unit = resolved_unit

            # If active document exists, set its unit system and linear unit preference
            # swUnitSystem_e (pref 263): 1=CGS, 2=MKS, 3=IPS, 4=Custom, 5=MMGS
            # swUnitsLinear (pref 47): 0=mm, 1=cm, 2=m, 3=in, 4=ft
            unit_sys_map = {"mm": 5, "m": 2, "cm": 1, "inch": 3, "in": 3}
            linear_unit_map = {"mm": 0, "m": 2, "cm": 1, "inch": 3, "in": 3, "ft": 4}

            doc = self.active_doc
            doc_units_updated = False
            read_sys = None
            read_lin = None
            if doc is not None:
                try:
                    sys_code = unit_sys_map.get(resolved_unit)
                    if sys_code is not None:
                        doc.SetUserPreferenceIntegerValue(263, sys_code)
                    else:
                        linear_code = linear_unit_map.get(resolved_unit)
                        if linear_code is not None:
                            doc.SetUserPreferenceIntegerValue(47, linear_code)
                    read_sys = doc.GetUserPreferenceIntegerValue(263)
                    read_lin = doc.GetUserPreferenceIntegerValue(47)
                    doc_units_updated = True
                except Exception as doc_ex:
                    logger.debug(f"Could not update active document unit preferences: {doc_ex}")

            res_data = {
                "unit": resolved_unit,
                "doc_updated": doc_units_updated,
            }
            if read_sys is not None:
                res_data["document_unit_system"] = read_sys
            if read_lin is not None:
                res_data["document_linear_unit"] = read_lin

            msg = (
                f"Default unit set to: {resolved_unit}"
                if doc_units_updated
                else f"No active document: unit converter set to {resolved_unit}; no document was changed"
            )
            return self._result(
                True,
                msg,
                SwErrors.swSuccess,
                res_data
            )
        except Exception as e:
            return self._result(False, f"Error setting units: {e}", SwErrors.swGeneralError)

    @property
    def app(self):
        """Get SolidWorks application object"""
        return self._sw_app
    
    # ========================================================================
    # Result Helper
    # ========================================================================
    
    def _result(self, success: bool, message: str,
                error_code: SwErrors = SwErrors.swSuccess,
                data: Optional[Dict] = None) -> Dict:
        """
        Create standardized result dictionary
        
        Args:
            success: Operation success status
            message: Human-readable message
            error_code: Error code enum
            data: Optional additional data
        
        Returns:
            Standardized result dictionary
        """
        err_val = getattr(error_code, "value", error_code)
        try:
            err_int = int(err_val)
        except Exception:
            err_int = int(SwErrors.swUnknownError)

        err_name = getattr(error_code, "name", None)
        if not err_name:
            try:
                err_name = SwErrors(err_int).name
            except Exception:
                err_name = "swUnknownError" if err_int != 0 else "swSuccess"

        result = {
            "success": success,
            "message": message,
            "error_code": err_int,
            "error_name": err_name,
            "timestamp": datetime.datetime.now().isoformat(),
            "data": data if data is not None else {},
        }
        return result
    
    # ========================================================================
    # Connection Methods
    # ========================================================================
    
    def _try_connect_com(self, target_pid: Optional[int] = None) -> bool:
        """
        Try connecting to SolidWorks via COM.
        If target_pid is specified, attempts to attach to that specific process.
        
        Args:
            target_pid: Optional target PID
            
        Returns:
            True if connection successful
        """
        pythoncom.CoInitialize()
        
        if target_pid is not None:
            # Check ROT for SolidWorks_PID_<target_pid>
            try:
                rot = pythoncom.GetRunningObjectTable()
                enum = rot.EnumRunning()
                ctx = pythoncom.CreateBindCtx()
                target_str = f"solidworks_pid_{target_pid}".lower()
                for m in enum:
                    try:
                        name = m.GetDisplayName(ctx, None).lower()
                        if target_str in name:
                            unk = rot.GetObject(m)
                            disp = unk.QueryInterface(pythoncom.IID_IDispatch)
                            app = win32com.client.Dispatch(disp)
                            if app is not None:
                                self._sw_app = app
                                self._connected_pid = target_pid
                                self._connected = True
                                logger.info(f"Connected to SolidWorks PID {target_pid} via ROT")
                                return True
                    except Exception:
                        pass
            except Exception as ex:
                logger.debug(f"ROT search for PID {target_pid} failed: {ex}")
            
            # Check GetActiveObject to see if it matches target_pid
            try:
                app = win32com.client.GetActiveObject("SldWorks.Application")
                if app is not None:
                    cur_pid = get_attr_or_call(app, "GetProcessID")
                    if cur_pid == target_pid:
                        self._sw_app = app
                        self._connected_pid = target_pid
                        self._connected = True
                        logger.info(f"Connected to SolidWorks PID {target_pid} via GetActiveObject")
                        return True
            except Exception as ex:
                logger.debug(f"GetActiveObject match for PID {target_pid} failed: {ex}")
            
            return False

        # No target PID: connect to running instance via get_running_app (never spawns processes)
        app = get_running_app()
        if app is not None:
            self._sw_app = app
            self._connected_pid = get_attr_or_call(self._sw_app, "GetProcessID")
            self._connected = True
            logger.info(f"Connected via get_running_app (PID={self._connected_pid})")
            return True

        return False

    def connect(self, pid: Optional[int] = None) -> Dict:
        """
        Connect to SolidWorks - launches if not running, or targets specific PID.
        If connecting to an existing running instance that is invisible (headless COM mode),
        connect() automatically restores its visibility (sets Visible=True) and appends
        '(made visible)' to the status message.
        
        Args:
            pid: Optional process ID of SolidWorks instance to connect to
            
        Returns:
            Result dictionary with connection status
        """
        try:
            logger.info(f"=== Connecting to SolidWorks (target pid={pid}) ===")
            
            # Step 0: Check if targeting a specific PID
            if pid is not None:
                running_pids = find_sldworks_pids()
                if pid not in running_pids:
                    return self._result(
                        False,
                        f"SolidWorks process with PID {pid} not found in running processes. Running SolidWorks PID(s): {running_pids}",
                        SwErrors.swProcessNotFound
                    )
                
                # Check for UAC elevation mismatch
                mismatch = check_uac_elevation_mismatch()
                if mismatch:
                    return self._result(False, mismatch, SwErrors.swConnectionError)

                if self._connected and self._sw_app is not None:
                    try:
                        cur_pid = get_attr_or_call(self._sw_app, "GetProcessID")
                        if cur_pid == pid:
                            version = get_attr_or_call(self._sw_app, "RevisionNumber")
                            was_invisible = False
                            try:
                                vis = self._sw_app.Visible
                                if not vis:
                                    was_invisible = True
                                    self._sw_app.Visible = True
                            except Exception:
                                pass
                            msg = f"Connected to targeted SolidWorks PID {pid} (version {version})"
                            if was_invisible:
                                msg += " (made visible)"
                            return self._result(
                                True,
                                msg,
                                SwErrors.swSuccess,
                                {"version": str(version), "pid": pid, "launched": False, "made_visible": was_invisible}
                            )
                    except Exception:
                        pass

                if self._try_connect_com(target_pid=pid):
                    version = get_attr_or_call(self._sw_app, "RevisionNumber")
                    real_pid = get_attr_or_call(self._sw_app, "GetProcessID")
                    if real_pid != pid:
                        self.disconnect()
                        return self._result(
                            False,
                            f"PID targeting mismatch: requested PID {pid}, but COM attached to PID {real_pid}.",
                            SwErrors.swConnectionError
                        )
                    was_invisible = False
                    try:
                        vis = self._sw_app.Visible
                        if not vis:
                            was_invisible = True
                            self._sw_app.Visible = True
                        self._sw_app.UserControl = True
                        if self._original_pref_toggle_11 is None:
                            self._original_pref_toggle_11 = self._sw_app.GetUserPreferenceToggle(11)
                        self._sw_app.SetUserPreferenceToggle(11, False)
                    except Exception:
                        pass
                    msg = f"Connected to targeted SolidWorks PID {pid} (version {version})"
                    if was_invisible:
                        msg += " (made visible)"
                    return self._result(
                        True,
                        msg,
                        SwErrors.swSuccess,
                        {"version": str(version), "pid": pid, "launched": False, "made_visible": was_invisible}
                    )
                else:
                    return self._result(
                        False,
                        f"Could not connect to SolidWorks PID {pid} via COM.",
                        SwErrors.swConnectionError
                    )

            # Step 1: Normal connection (no specific PID requested)
            if self.is_connected:
                version = get_attr_or_call(self._sw_app, "RevisionNumber")
                cur_pid = get_attr_or_call(self._sw_app, "GetProcessID") or getattr(self, "_connected_pid", None)
                was_invisible = False
                try:
                    vis = self._sw_app.Visible
                    if not vis:
                        was_invisible = True
                        self._sw_app.Visible = True
                except Exception:
                    pass
                msg = f"Already connected to SolidWorks {version}"
                if was_invisible:
                    msg += " (made visible)"
                return self._result(
                    True,
                    msg,
                    SwErrors.swSuccess,
                    {"version": str(version), "pid": cur_pid, "launched": False, "made_visible": was_invisible}
                )

            # Check UAC elevation mismatch before trying
            mismatch = check_uac_elevation_mismatch()
            if mismatch:
                return self._result(False, mismatch, SwErrors.swConnectionError)

            # Check if running instance is blocked by a modal dialog
            running_pids = find_sldworks_pids()
            if running_pids:
                blocking_dialog = find_blocking_modal_dialog()
                if blocking_dialog:
                    raise SolidWorksModalBlockedException(
                        f"SolidWorks is blocked by a modal dialog: {blocking_dialog.get('title')}",
                        blocking_dialog
                    )

            # Try connecting to running instance
            if self._try_connect_com():
                version = get_attr_or_call(self._sw_app, "RevisionNumber")
                cur_pid = get_attr_or_call(self._sw_app, "GetProcessID") or getattr(self, "_connected_pid", None)
                was_invisible = False
                try:
                    vis = self._sw_app.Visible
                    if not vis:
                        was_invisible = True
                        self._sw_app.Visible = True
                    self._sw_app.UserControl = True
                    if self._original_pref_toggle_11 is None:
                        self._original_pref_toggle_11 = self._sw_app.GetUserPreferenceToggle(11)
                    self._sw_app.SetUserPreferenceToggle(11, False)
                except Exception as ex:
                    logger.debug(f"Could not adjust swFullyConstrainedSketchMode: {ex}")

                msg = f"Connected to SolidWorks {version}"
                if was_invisible:
                    msg += " (made visible)"
                return self._result(
                    True,
                    msg,
                    SwErrors.swSuccess,
                    {"version": str(version), "pid": cur_pid, "launched": False, "made_visible": was_invisible}
                )

            # Step 1.5: If SolidWorks process(es) are already running, DO NOT launch another instance!
            running_pids = find_sldworks_pids()
            if running_pids:
                blocking_dialog = find_blocking_modal_dialog()
                if blocking_dialog:
                    return self._result(
                        False,
                        f"SolidWorks process(es) already running (PID(s): {running_pids}) but blocked by modal dialog: '{blocking_dialog.get('title')}'. Dismiss dialog first.",
                        SwErrors.swConnectionError,
                        {"running_pids": running_pids, "blocked_by_dialog": True, "dialog": blocking_dialog}
                    )
                logger.info(f"SolidWorks process(es) already running (PID(s): {running_pids}). Waiting for COM responsiveness...")
                for attempt in range(5):
                    time.sleep(1.0)
                    if self._try_connect_com():
                        version = get_attr_or_call(self._sw_app, "RevisionNumber")
                        cur_pid = get_attr_or_call(self._sw_app, "GetProcessID") or getattr(self, "_connected_pid", None)
                        was_invisible = False
                        try:
                            vis = self._sw_app.Visible
                            if not vis:
                                was_invisible = True
                                self._sw_app.Visible = True
                            self._sw_app.UserControl = True
                            if self._original_pref_toggle_11 is None:
                                self._original_pref_toggle_11 = self._sw_app.GetUserPreferenceToggle(11)
                            self._sw_app.SetUserPreferenceToggle(11, False)
                        except Exception:
                            pass
                        msg = f"Connected to SolidWorks {version}"
                        if was_invisible:
                            msg += " (made visible)"
                        return self._result(
                            True,
                            msg,
                            SwErrors.swSuccess,
                            {"version": str(version), "pid": cur_pid, "launched": False, "made_visible": was_invisible}
                        )
                # Running processes exist but none is attachable (not in the ROT)
                unattachable = [get_process_details(p) for p in running_pids]
                any_com = any(p.get("is_com_launched", False) for p in unattachable)
                if any_com:
                    reason = "were started by COM automation, cannot be attached, and must be closed by the user"
                else:
                    reason = "not attachable (not in the ROT): busy, elevated or run by another user"

                msg = (
                    f"SolidWorks process(es) exist (PID(s): {running_pids}) but {reason}. "
                    f"Refusing to launch a duplicate instance."
                )
                return self._result(
                    False,
                    msg,
                    SwErrors.swConnectionError,
                    {"unattachable_processes": unattachable, "running_pids": running_pids}
                )

            # Step 2: 0 running -> Launch SolidWorks using launch_solidworks()
            app, err_msg = launch_solidworks(
                exe_path=self._sw_exe_path if self._config.exe_path == "auto" else self._config.exe_path,
                startup_timeout=self._config.startup_timeout,
                retry_interval=self._config.connection_retry_interval,
            )
            if app is None:
                return self._result(False, err_msg or "Failed to launch SolidWorks.", SwErrors.swConnectionError)

            self._sw_app = app
            self._connected = True
            self._connected_pid = get_attr_or_call(app, "GetProcessID")
            version = get_attr_or_call(app, "RevisionNumber")
            try:
                self._sw_app.Visible = True
                self._sw_app.UserControl = True
                if self._original_pref_toggle_11 is None:
                    self._original_pref_toggle_11 = self._sw_app.GetUserPreferenceToggle(11)
                self._sw_app.SetUserPreferenceToggle(11, False)
            except Exception:
                pass
            return self._result(
                True,
                f"Launched and connected to SolidWorks {version}",
                SwErrors.swSuccess,
                {"version": str(version), "pid": self._connected_pid, "launched": True}
            )

        except SolidWorksModalBlockedException:
            raise
        except Exception as e:
            logger.error(f"Connection error: {e}\n{traceback.format_exc()}")
            return self._result(False, f"Connection error: {e}", SwErrors.swConnectionError)

    def disconnect(self) -> Dict:
        """
        Disconnect from SolidWorks (does not close SolidWorks).
        Restores modified user preference toggles.
        
        Returns:
            Result dictionary
        """
        restored_prefs = []
        if self._sw_app is not None and self._original_pref_toggle_11 is not None:
            try:
                self._sw_app.SetUserPreferenceToggle(11, self._original_pref_toggle_11)
                restored_prefs.append("swFullyConstrainedSketchMode")
            except Exception as ex:
                logger.debug(f"Could not restore user preference toggle 11: {ex}")
            self._original_pref_toggle_11 = None

        self._sw_app = None
        self._connected = False
        self._connected_pid = None
        logger.info("Disconnected from SolidWorks")
        restored_str = f" (restored {', '.join(restored_prefs)})" if restored_prefs else ""
        return self._result(True, f"Disconnected from SolidWorks{restored_str}")

    # ========================================================================
    # Document Methods
    # ========================================================================

    def get_active_doc(self) -> Tuple[Any, Optional[Dict]]:
        """
        Get active document with auto-connect and auto-reconnect on RPC disconnect.
        
        Returns:
            Tuple of (document, error_result)
            - If successful: (document, None)
            - If failed: (None, error_dict)
        """
        if not self.is_connected:
            if not getattr(self._config, "auto_connect", True):
                return None, self._result(
                    False,
                    "Not connected to SolidWorks. Call connect_solidworks first.",
                    SwErrors.swConnectionError
                )
            result = self.connect()
            if not result["success"]:
                return None, result

        try:
            doc = self._sw_app.ActiveDoc
        except Exception as ex:
            if is_rpc_disconnect_error(ex):
                logger.warning(f"RPC server unavailable ({ex}) during get_active_doc. Attempting auto-reconnect once...")
                self._connected = False
                self._sw_app = None
                self._connected_pid = None
                reconn = self.connect()
                if not reconn.get("success"):
                    return None, reconn
                try:
                    doc = self._sw_app.ActiveDoc
                except Exception as retry_ex:
                    return None, self._result(False, f"Failed to get ActiveDoc after reconnect: {retry_ex}", SwErrors.swConnectionError)
            else:
                raise

        if doc is None:
            return None, self._result(
                False,
                "No document open. Use create_new_part first.",
                SwErrors.swNoActiveDocument
            )

        return doc, None
    
    def _get_doc_title(self, doc) -> str:
        """Get document title (handles property/method difference)"""
        try:
            return get_attr_or_call(doc, "GetTitle") or "Unknown"
        except Exception:
            return "Unknown"
    
    def _get_doc_path(self, doc) -> str:
        """Get document path (handles property/method difference)"""
        try:
            return get_attr_or_call(doc, "GetPathName") or ""
        except Exception:
            return ""

    def get_document_type(self, doc=None) -> str:
        """
        Get document type as a normalized string: 'Part', 'Assembly', 'Drawing', or 'Unknown'.
        """
        if doc is None:
            if not self.is_connected or not self._sw_app:
                return "Unknown"
            try:
                doc = getattr(self._sw_app, "ActiveDoc", None)
            except Exception:
                doc = None
            if doc is None:
                return "Unknown"

        try:
            dtype_raw = get_attr_or_call(doc, "GetType")
            if dtype_raw is None:
                dtype_raw = getattr(doc, "GetType", 0)
                if callable(dtype_raw):
                    dtype_raw = dtype_raw()
            type_map = {
                1: "Part",
                2: "Assembly",
                3: "Drawing",
            }
            return type_map.get(dtype_raw, "Unknown")
        except Exception:
            return "Unknown"
