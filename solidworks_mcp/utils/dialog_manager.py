"""
SolidWorks Dialog Manager & Win32 Recovery Engine
-------------------------------------------------
Inspects, reads, and programmatically controls modal dialogs, error windows,
and prompts spawned by SolidWorks (SLDWORKS.exe).

Overcomes the Single-Threaded Apartment (STA) freeze by allowing out-of-band
dialog enumeration and control across Windows desktops.
"""

import sys
import time
import logging
import threading
import ctypes
from ctypes import wintypes
from typing import List, Dict, Any, Optional, Tuple

from ..constants import SwErrors
from ..config import get_config

logger = logging.getLogger("SolidWorksMCP.DialogManager")

# Win32 Constants
WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
WM_COMMAND = 0x0111
WM_CLOSE = 0x0010
BM_CLICK = 0x00F5
BN_CLICKED = 0

IDOK = 1
IDCANCEL = 2
IDABORT = 3
IDRETRY = 4
IDIGNORE = 5
IDYES = 6
IDNO = 7
IDCLOSE = 8

DESKTOP_READOBJECTS = 0x0001
DESKTOP_WRITEOBJECTS = 0x0002
DESKTOP_ENUMERATE = 0x0040
DESKTOP_ALL_ACCESS = 0x01FF

TH32CS_SNAPPROCESS = 0x00000002

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("thProcessClassID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_char * 260),
    ]


def get_sldworks_pids() -> List[int]:
    """Find all running SolidWorks process IDs using Toolhelp API."""
    pids = []
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == -1:
        return pids

    try:
        pe = PROCESSENTRY32()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
        if kernel32.Process32First(snapshot, ctypes.byref(pe)):
            while True:
                exe_name = pe.szExeFile.decode("latin-1", errors="ignore").lower()
                if "sldworks" in exe_name:
                    pids.append(pe.th32ProcessID)
                if not kernel32.Process32Next(snapshot, ctypes.byref(pe)):
                    break
    finally:
        kernel32.CloseHandle(snapshot)

    return pids


def _get_window_text_safe(hwnd: int) -> str:
    """Safely get window text using WM_GETTEXT with timeout to avoid deadlocks."""
    length = wintypes.DWORD()
    # SendMessageTimeout to avoid hanging if the window is unresponsive
    SMTO_ABORTIFHUNG = 0x0002
    res = user32.SendMessageTimeoutW(
        hwnd, WM_GETTEXTLENGTH, 0, 0,
        SMTO_ABORTIFHUNG, 500, ctypes.byref(length)
    )
    if not res or length.value <= 0:
        # Fallback to GetWindowTextW
        buf = ctypes.create_unicode_buffer(1024)
        user32.GetWindowTextW(hwnd, buf, 1024)
        return buf.value.strip()

    buf = ctypes.create_unicode_buffer(length.value + 2)
    copied = wintypes.DWORD()
    res = user32.SendMessageTimeoutW(
        hwnd, WM_GETTEXT, length.value + 1, ctypes.byref(buf),
        SMTO_ABORTIFHUNG, 500, ctypes.byref(copied)
    )
    if res:
        return buf.value.strip()

    buf = ctypes.create_unicode_buffer(1024)
    user32.GetWindowTextW(hwnd, buf, 1024)
    return buf.value.strip()


def _get_window_class_safe(hwnd: int) -> str:
    """Get window class name."""
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value.strip()


def _get_window_rect_safe(hwnd: int) -> Dict[str, int]:
    """Get window bounding rectangle."""
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return {
        "left": rect.left,
        "top": rect.top,
        "right": rect.right,
        "bottom": rect.bottom,
        "width": rect.right - rect.left,
        "height": rect.bottom - rect.top,
    }


def inspect_child_controls(hwnd: int) -> Dict[str, Any]:
    """Inspect all child controls of a window (labels, edits, buttons)."""
    texts = []
    buttons = []
    edits = []

    def child_enum_cb(child_hwnd, lparam):
        if not user32.IsWindow(child_hwnd):
            return True

        cls = _get_window_class_safe(child_hwnd).lower()
        text = _get_window_text_safe(child_hwnd)
        ctrl_id = user32.GetDlgCtrlID(child_hwnd)
        is_visible = bool(user32.IsWindowVisible(child_hwnd))
        is_enabled = bool(user32.IsWindowEnabled(child_hwnd))

        if "button" in cls:
            buttons.append({
                "hwnd": child_hwnd,
                "id": ctrl_id,
                "text": text,
                "class": cls,
                "visible": is_visible,
                "enabled": is_enabled,
            })
        elif "edit" in cls:
            if text:
                edits.append({
                    "hwnd": child_hwnd,
                    "id": ctrl_id,
                    "text": text,
                    "visible": is_visible,
                })
        elif "static" in cls or "label" in cls:
            if text:
                texts.append(text)
        elif text and is_visible:
            texts.append(text)

        return True

    cb = WNDENUMPROC(child_enum_cb)
    user32.EnumChildWindows(hwnd, cb, 0)

    return {
        "messages": texts,
        "buttons": buttons,
        "edits": edits,
    }


def get_all_solidworks_windows(extra_pids: Optional[Any] = None) -> List[Dict[str, Any]]:
    """
    Enumerate all windows associated with SolidWorks across both current
    and Default interactive desktops.
    """
    sw_pids = set(get_sldworks_pids())
    if extra_pids:
        sw_pids.update(extra_pids)
    collected_hwnds = set()
    windows = []

    def process_window(hwnd: int):
        if hwnd in collected_hwnds:
            return
        collected_hwnds.add(hwnd)

        if not user32.IsWindow(hwnd):
            return

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        title = _get_window_text_safe(hwnd)
        cls = _get_window_class_safe(hwnd)
        is_visible = bool(user32.IsWindowVisible(hwnd))

        # Check if window belongs to SolidWorks or is a SolidWorks dialog
        is_sw_pid = pid.value in sw_pids
        is_sw_title = (
            "solidworks" in title.lower()
            or "sldworks" in title.lower()
            or "what's wrong" in title.lower()
            or "whats wrong" in title.lower()
        )
        is_dialog_class = cls in ["#32770", "AfxWnd140u", "Qt5QWindowIcon"]

        if not (is_sw_pid or is_sw_title):
            return

        # Distinguish dialogs / popups from main background window
        rect = _get_window_rect_safe(hwnd)
        is_dialog = (cls == "#32770") or ("dialog" in cls.lower()) or (rect["width"] < 1200 and rect["height"] < 900 and title)

        controls = inspect_child_controls(hwnd) if (is_dialog or is_visible) else {"messages": [], "buttons": [], "edits": []}

        windows.append({
            "hwnd": hwnd,
            "pid": pid.value,
            "title": title,
            "class": cls,
            "visible": is_visible,
            "is_dialog": is_dialog,
            "rect": rect,
            "messages": controls["messages"],
            "buttons": controls["buttons"],
            "edits": controls["edits"],
        })

    def enum_cb(hwnd, lparam):
        process_window(hwnd)
        return True

    cb = WNDENUMPROC(enum_cb)

    # 1. Enumerate on current desktop
    user32.EnumWindows(cb, 0)

    # 2. Enumerate on all desktops of WinSta0 if accessible
    try:
        hwinsta = user32.OpenWindowStationW("WinSta0", False, 0x037F)
        if hwinsta:
            DESKENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_wchar_p, ctypes.c_void_p)
            def desk_cb(desk_name, _):
                hdesk = user32.OpenDesktopW(desk_name, 0, False, DESKTOP_ENUMERATE | DESKTOP_READOBJECTS)
                if hdesk:
                    try:
                        user32.EnumDesktopWindows(hdesk, cb, 0)
                    finally:
                        user32.CloseDesktop(hdesk)
                return True

            try:
                user32.EnumDesktopsW(hwinsta, DESKENUMPROC(desk_cb), 0)
            finally:
                user32.CloseWindowStation(hwinsta)
    except Exception as e:
        logger.debug(f"EnumDesktopsW skipped: {e}")

    return windows


def list_active_dialogs(target_pid: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    Return only active/visible blocking modal dialogs in SolidWorks.

    Invariants:
    1. NEVER returns the SolidWorks main frame window or its child panes.
    2. SolidWorks main window MUST be disabled (IsWindowEnabled == False).
       If main window is enabled, no blocking modal exists; returns [].
    3. Candidate window must be top-level (not WS_CHILD), visible,
       owned by the SolidWorks main window (GetWindow GW_OWNER or GA_ROOTOWNER)
       OR a top-level #32770 dialog / dialog class belonging to SolidWorks or its main thread,
       and hwnd != main_hwnd.
    """
    main_hwnd = get_main_solidworks_hwnd(target_pid)
    if not main_hwnd or user32.IsWindowEnabled(main_hwnd):
        return []

    main_pid = wintypes.DWORD()
    main_tid = user32.GetWindowThreadProcessId(main_hwnd, ctypes.byref(main_pid))

    sw_pids = set([target_pid] if target_pid else get_sldworks_pids())
    collected_hwnds = set()
    dialogs = []

    GWL_STYLE = -16
    WS_CHILD = 0x40000000

    def process_dialog_candidate(hwnd: int):
        if hwnd in collected_hwnds:
            return
        collected_hwnds.add(hwnd)

        if not user32.IsWindow(hwnd):
            return
        if hwnd == main_hwnd:
            return
        if not user32.IsWindowVisible(hwnd):
            return

        # Must not be a child window (must be top-level)
        style = user32.GetWindowLongW(hwnd, GWL_STYLE)
        if style & WS_CHILD:
            return

        # Must belong to SolidWorks process
        pid = wintypes.DWORD()
        tid = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if sw_pids and pid.value not in sw_pids:
            return

        title = _get_window_text_safe(hwnd)
        cls = _get_window_class_safe(hwnd)
        rect = _get_window_rect_safe(hwnd)

        # Exclude main application window, large document frames, or child panes
        if cls.startswith("Afx:") and rect.get("width", 0) > 500 and rect.get("height", 0) > 400:
            return
        if cls in ["SysShadow", "Internet Explorer_Hidden", "tooltips_class32"]:
            return
        if any(term in title.lower() for term in ["student edition", "solidworks standard", "solidworks professional", "solidworks premium"]):
            if cls != "#32770":
                return

        owner = user32.GetWindow(hwnd, 4)  # GW_OWNER
        root_owner = user32.GetAncestor(hwnd, 3)  # GA_ROOTOWNER

        is_owned = (owner == main_hwnd or root_owner == main_hwnd)
        is_dialog_cls = (cls == "#32770" or "dialog" in cls.lower())

        # Dialog must be owned by main window or standard dialog class
        if not (is_owned or is_dialog_cls):
            return

        attach_to_window_desktop(hwnd)
        controls = inspect_child_controls(hwnd)

        # Must have a title or buttons
        if not title and not controls["buttons"] and cls != "#32770":
            return

        dialogs.append({
            "hwnd": hwnd,
            "pid": pid.value,
            "tid": tid,
            "title": title,
            "class": cls,
            "visible": True,
            "is_dialog": True,
            "rect": rect,
            "messages": controls["messages"],
            "buttons": controls["buttons"],
            "edits": controls["edits"],
        })

    def enum_cb(hwnd, lparam):
        process_dialog_candidate(hwnd)
        return True

    cb = WNDENUMPROC(enum_cb)
    user32.EnumWindows(cb, 0)

    # Also enumerate across WinSta0 desktops if accessible
    try:
        hwinsta = user32.OpenWindowStationW("WinSta0", False, 0x037F)
        if hwinsta:
            DESKENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_wchar_p, ctypes.c_void_p)
            def desk_cb(desk_name, _):
                hdesk = user32.OpenDesktopW(desk_name, 0, False, DESKTOP_ENUMERATE | DESKTOP_READOBJECTS)
                if hdesk:
                    try:
                        user32.EnumDesktopWindows(hdesk, cb, 0)
                    finally:
                        user32.CloseDesktop(hdesk)
                return True

            try:
                user32.EnumDesktopsW(hwinsta, DESKENUMPROC(desk_cb), 0)
            finally:
                user32.CloseWindowStation(hwinsta)
    except Exception as e:
        logger.debug(f"EnumDesktopsW skipped: {e}")

    return dialogs


def get_main_solidworks_hwnd(target_pid: Optional[int] = None) -> Optional[int]:
    """Find the top-level main frame window handle for SolidWorks across all desktops."""
    sw_pids = set([target_pid] if target_pid else get_sldworks_pids())
    if not sw_pids:
        return None

    main_hwnd = None

    def enum_main_cb(hwnd, _):
        nonlocal main_hwnd
        if not user32.IsWindow(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in sw_pids:
            owner = user32.GetWindow(hwnd, 4)  # GW_OWNER
            if owner == 0:
                cls_buf = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, cls_buf, 256)
                title_buf = ctypes.create_unicode_buffer(512)
                user32.GetWindowTextW(hwnd, title_buf, 512)
                rect = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                width = rect.right - rect.left
                height = rect.bottom - rect.top
                if width > 500 and height > 400:
                    cls_str = cls_buf.value.lower()
                    title_str = title_buf.value.lower()
                    if "solidworks" in title_str or "toplevelframe" in cls_str or cls_buf.value.startswith("Afx:"):
                        main_hwnd = hwnd
                        return False
        return True

    cb = WNDENUMPROC(enum_main_cb)
    user32.EnumWindows(cb, 0)
    if not main_hwnd:
        try:
            hwinsta = user32.OpenWindowStationW("WinSta0", False, 0x037F)
            if hwinsta:
                DESKENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_wchar_p, ctypes.c_void_p)
                def desk_cb(desk_name, _):
                    hdesk = user32.OpenDesktopW(desk_name, 0, False, DESKTOP_ENUMERATE | DESKTOP_READOBJECTS)
                    if hdesk:
                        try:
                            user32.EnumDesktopWindows(hdesk, cb, 0)
                        finally:
                            user32.CloseDesktop(hdesk)
                    return True if main_hwnd is None else False

                try:
                    user32.EnumDesktopsW(hwinsta, DESKENUMPROC(desk_cb), 0)
                finally:
                    user32.CloseWindowStation(hwinsta)
        except Exception as e:
            logger.debug(f"EnumDesktopsW skipped in get_main_solidworks_hwnd: {e}")

    return main_hwnd


def find_blocking_modal_dialog(target_pid: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """
    Detect if SolidWorks is blocked by an active modal dialog.
    A dialog counts as blocking ONLY if:
    1. SolidWorks main window is disabled (IsWindowEnabled == False).
    2. Window is top-level, visible, and belongs to SolidWorks (#32770 or owned popup).
    """
    dialogs = list_active_dialogs(target_pid=target_pid)
    return dialogs[0] if dialogs else None


def attach_to_window_desktop(hwnd: int) -> bool:
    """Ensure calling thread's desktop is attached to the desktop containing hwnd."""
    if user32.IsWindow(hwnd):
        return True
    hwinsta = user32.OpenWindowStationW("WinSta0", False, 0x037F)
    if not hwinsta:
        return False
    DESKENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_wchar_p, ctypes.c_void_p)
    found = False

    def desk_cb(name, _):
        nonlocal found
        hdesk = user32.OpenDesktopW(name, 0, False, 0x01FF)
        if hdesk:
            user32.SetThreadDesktop(hdesk)
            if user32.IsWindow(hwnd):
                found = True
                return False
        return True

    try:
        user32.EnumDesktopsW(hwinsta, DESKENUMPROC(desk_cb), 0)
    finally:
        user32.CloseWindowStation(hwinsta)
    return found


def dismiss_dialog(
    hwnd: Optional[int] = None,
    action: str = "OK",
    force_close: bool = False
) -> Dict[str, Any]:
    """
    Dismiss a dialog window by clicking a specified button or sending close signal.
    If hwnd is None or 0, targets the currently detected blocking modal dialog.
    Refuses action if hwnd is not the currently detected blocking modal dialog.
    Verifies that the window handle (HWND) is destroyed after action.
    """
    # Guard: Must refuse (error, click nothing) if hwnd is not the currently detected modal
    blocking = find_blocking_modal_dialog()
    if not blocking:
        return {
            "success": False,
            "message": f"No blocking modal dialog detected in SolidWorks. Refusing to interact with HWND {hwnd} to prevent accidental clicks on the main frame.",
            "error_code": int(SwErrors.swInvalidInput),
            "data": {"hwnd": hwnd}
        }

    blocking_hwnd = blocking.get("hwnd")
    if hwnd is None or hwnd == 0:
        hwnd = blocking_hwnd
    elif hwnd != blocking_hwnd:
        return {
            "success": False,
            "message": f"HWND {hwnd} is not the currently detected blocking modal dialog (detected active modal HWND is {blocking_hwnd}, title: '{blocking.get('title')}'). Refusing action to protect main application window.",
            "error_code": int(SwErrors.swInvalidInput),
            "data": {"requested_hwnd": hwnd, "blocking_hwnd": blocking_hwnd, "title": blocking.get("title")}
        }

    if not attach_to_window_desktop(hwnd):
        return {"success": False, "message": f"Invalid or non-existent HWND: {hwnd}", "error_code": int(SwErrors.swInvalidInput)}

    if not user32.IsWindow(hwnd):
        return {"success": False, "message": f"Window HWND {hwnd} does not exist.", "error_code": int(SwErrors.swInvalidInput)}

    title = _get_window_text_safe(hwnd)

    if force_close:
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        start_wait = time.time()
        while user32.IsWindow(hwnd) and (time.time() - start_wait < 1.5):
            time.sleep(0.05)
        if user32.IsWindow(hwnd):
            return {
                "success": False,
                "message": f"Sent WM_CLOSE to window '{title}' (HWND: {hwnd}), but window did not close.",
                "error_code": int(SwErrors.swOperationFailed),
            }
        return {
            "success": True,
            "message": f"Sent WM_CLOSE to window '{title}' (HWND: {hwnd}) and verified window closed.",
            "action": "force_close"
        }

    controls = inspect_child_controls(hwnd)
    buttons = controls.get("buttons", [])

    action_norm = action.strip().lower()

    # Exact (case-insensitive) button text matching (with or without accelerator ampersand)
    target_button = None
    for btn in buttons:
        btn_text = btn.get("text", "").strip().lower()
        if btn_text == action_norm or btn_text.replace("&", "") == action_norm.replace("&", ""):
            target_button = btn
            break

    # If button not found by text, try standard control IDs
    if not target_button:
        id_map = {
            "ok": IDOK,
            "cancel": IDCANCEL,
            "yes": IDYES,
            "no": IDNO,
            "close": IDCANCEL,
            "abort": IDABORT,
            "retry": IDRETRY,
            "ignore": IDIGNORE,
        }
        target_id = id_map.get(action_norm)
        if target_id is not None:
            for btn in buttons:
                if btn.get("id") == target_id:
                    target_button = btn
                    break

    # If not matched by standard ID, try prefix or substring matching
    if not target_button:
        for btn in buttons:
            clean_btn = btn.get("text", "").strip().lower().replace("&", "")
            clean_act = action_norm.replace("&", "")
            if clean_btn.startswith(clean_act) or (clean_act in clean_btn and len(clean_act) >= 2):
                target_button = btn
                break

    # If no button matches, return an error and leave the dialog open
    if not target_button:
        btn_names = [b.get("text", "") for b in buttons if b.get("text")]
        return {
            "success": False,
            "message": f"No button matching '{action}' found on dialog '{title}' (HWND: {hwnd}). Available buttons: {btn_names}. Dialog remains open.",
            "error_code": int(SwErrors.swInvalidInput),
            "data": {"available_buttons": btn_names, "dialog_hwnd": hwnd, "title": title}
        }

    btn_hwnd = target_button["hwnd"]
    btn_id = target_button["id"]

    # Send BM_CLICK with timeout to prevent deadlocks across thread/desktop boundaries
    res_val = wintypes.DWORD()
    SMTO_ABORTIFHUNG = 0x0002
    sent = user32.SendMessageTimeoutW(
        btn_hwnd, BM_CLICK, 0, 0,
        SMTO_ABORTIFHUNG, 2000, ctypes.byref(res_val)
    )
    if not sent:
        user32.PostMessageW(btn_hwnd, BM_CLICK, 0, 0)

    # Verify if window closed; if not, try TaskDialog / DirectUI and UIAutomation fallbacks
    start_wait = time.time()
    while user32.IsWindow(hwnd) and (time.time() - start_wait < 1.0):
        time.sleep(0.05)

    if user32.IsWindow(hwnd):
        # Fallback 1: Post WM_COMMAND to button parent (e.g. CtrlNotifySink in DirectUI)
        parent = user32.GetParent(btn_hwnd)
        if parent:
            user32.PostMessageW(parent, 0x0111, btn_id, btn_hwnd)

        # Fallback 2: Post WM_COMMAND and TDM_CLICK_BUTTON to dialog
        cmd_id = 7 if "no" in action_norm else (6 if "yes" in action_norm else 1)
        user32.PostMessageW(hwnd, 0x0111, cmd_id, 0)
        user32.PostMessageW(hwnd, 0x0466, cmd_id, 0)

        # Fallback 3: UIAutomation Invoke
        try:
            import comtypes.client
            uia_mod = comtypes.client.GetModule("UIAutomationCore.dll")
            uia = comtypes.client.CreateObject("{ff48dba4-60ef-4201-aa87-54103eef594e}", interface=uia_mod.IUIAutomation)
            btn_el = uia.ElementFromHandle(btn_hwnd)
            p = btn_el.GetCurrentPattern(uia_mod.UIA_InvokePatternId)
            if p:
                inv = p.QueryInterface(uia_mod.IUIAutomationInvokePattern)
                inv.Invoke()
            else:
                leg = btn_el.GetCurrentPattern(uia_mod.UIA_LegacyIAccessiblePatternId)
                if leg:
                    leg_acc = leg.QueryInterface(uia_mod.IUIAutomationLegacyIAccessiblePattern)
                    leg_acc.DoDefaultAction()
        except Exception as uia_ex:
            logger.debug(f"UIA fallback in dismiss_dialog: {uia_ex}")

        # Final verification
        start_wait = time.time()
        while user32.IsWindow(hwnd) and (time.time() - start_wait < 1.5):
            time.sleep(0.05)

    if user32.IsWindow(hwnd):
        return {
            "success": False,
            "message": f"Clicked button '{target_button['text']}' (ID: {btn_id}), but dialog '{title}' (HWND: {hwnd}) is still open.",
            "error_code": int(SwErrors.swOperationFailed),
            "button": target_button,
        }

    return {
        "success": True,
        "message": f"Clicked button '{target_button['text']}' (ID: {btn_id}) on dialog '{title}' and verified dialog dismissed.",
        "button": target_button,
    }


def auto_resolve_blocking_dialogs() -> List[Dict[str, Any]]:
    """
    Inspect all open dialogs and resolve safe non-destructive prompts automatically
    IF AND ONLY IF auto_dismiss_known_dialogs is enabled in configuration.
    Policy: auto-resolution is OFF by default (config.auto_dismiss_known_dialogs=False).
    When disabled, known dialogs are NOT dismissed, allowing swModalDialogBlocking
    to be raised with title, buttons, and hwnd.
    When enabled, matches by exact title AND exact button set.
    """
    cfg = get_config()
    if not getattr(cfg, "auto_dismiss_known_dialogs", False):
        logger.debug("auto_resolve_blocking_dialogs: auto-resolution is OFF by default.")
        return []

    dialogs = list_active_dialogs()
    results = []

    for diag in dialogs:
        raw_title = diag.get("title", "").strip()
        title = raw_title.lower()
        hwnd = diag["hwnd"]
        buttons = diag.get("buttons", [])
        norm_buttons = {b.get("text", "").replace("&", "").strip().lower() for b in buttons}

        resolved = False
        action_taken = ""

        # What's Wrong dialog
        if title in ["what's wrong", "whats wrong"] and ("close" in norm_buttons or "cancel" in norm_buttons):
            close_btn = "close" if "close" in norm_buttons else "cancel"
            res = dismiss_dialog(hwnd, close_btn)
            if res.get("success"):
                action_taken = f"Closed What's Wrong dialog using button '{close_btn}'"
                resolved = True

        # 3DEXPERIENCE Marketplace prompt:
        # Match exact title "SOLIDWORKS" AND exact button set
        elif title == "solidworks" and norm_buttons == {
            "yes, enable 3dexperience marketplace.",
            "no, disable 3dexperience marketplace.",
        }:
            target_act = "No, disable"
            res = dismiss_dialog(hwnd, target_act)
            if res.get("success"):
                action_taken = (
                    "Dismissed 3DEXPERIENCE Marketplace prompt using button 'No, disable...' "
                    "(Note: this changes a SolidWorks setting)"
                )
                resolved = True
        else:
            logger.warning(
                f"Dialog '{raw_title}' (HWND: {hwnd}) with buttons {norm_buttons} is not in auto-resolve allowlist. "
                f"Waiting for explicit dismiss_dialog tool call."
            )
            resolved = False

        if resolved:
            results.append({
                "hwnd": hwnd,
                "title": raw_title,
                "action": action_taken,
                "details": res
            })

    return results


class DialogWatchdog:
    """
    Background watchdog thread that continuously checks for blocking modal
    dialogs during long COM operations.
    """

    def __init__(self, check_interval: float = 0.5, auto_dismiss: bool = False):
        self._interval = check_interval
        self._auto_dismiss = auto_dismiss
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.detected_dialogs: List[Dict[str, Any]] = []

    def start(self):
        """Start the watchdog thread."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(target=self._run, daemon=True, name="SWDialogWatchdog")
            self._thread.start()
            logger.info("Dialog watchdog started (auto_dismiss=%s).", self._auto_dismiss)

    def stop(self):
        """Stop the watchdog thread."""
        with self._lock:
            self._running = False
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=2.0)
            logger.info("Dialog watchdog stopped.")

    def _run(self):
        while self._running:
            try:
                dialogs = list_active_dialogs()
                if dialogs:
                    for d in dialogs:
                        logger.warning(f"Watchdog detected dialog: {d.get('title')} (HWND: {d.get('hwnd')})")
                        self.detected_dialogs.append(d)
                    if self._auto_dismiss:
                        auto_resolve_blocking_dialogs()
            except Exception as e:
                logger.error(f"Watchdog error: {e}")
            time.sleep(self._interval)
