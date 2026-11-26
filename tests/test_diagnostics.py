"""
Dialog detection, dismissal and auto-resolve, exercised against real Win32 dialogs.

The dialog manager only touches a dialog that is blocking SolidWorks: it must be owned
by the SolidWorks main frame, and that frame must be disabled by the modal. These tests
build exactly that situation inside the test process. A fake "SOLIDWORKS" frame window
owns a real MessageBox, and the manager is pointed at this process instead of
SLDWORKS.exe. A SolidWorks session open on the machine is never touched.
"""

import ctypes
import os
import threading
import time
from ctypes import wintypes
from types import SimpleNamespace

import pytest

from solidworks_mcp.utils import dialog_manager as dm
from solidworks_mcp.server import sw_automation, _tool_definitions

# A private user32 handle, so these prototypes do not change the module-wide windll.user32.
_u32 = ctypes.WinDLL("user32", use_last_error=True)
_u32.CreateWindowExW.restype = wintypes.HWND
_u32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]
_u32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
_u32.DestroyWindow.argtypes = [wintypes.HWND]
_u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_u32.IsWindow.argtypes = [wintypes.HWND]
_u32.IsWindowEnabled.argtypes = [wintypes.HWND]
_ENUM_THREAD_WNDPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_u32.EnumThreadWindows.argtypes = [wintypes.DWORD, _ENUM_THREAD_WNDPROC, wintypes.LPARAM]

WS_OVERLAPPEDWINDOW = 0x00CF0000
WM_CLOSE = 0x0010
MB_OK, MB_OKCANCEL = 0x0, 0x1


class FakeSolidWorksModal:
    """A 'SOLIDWORKS' frame owning a MessageBox, on its own thread, the way SolidWorks shows a prompt."""

    def __init__(self, text: str, title: str, mb_type: int = MB_OKCANCEL):
        self.text, self.title, self.mb_type = text, title, mb_type
        self.frame = None
        self.thread_id = None
        self.result = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        self.thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        self.frame = _u32.CreateWindowExW(
            0, "STATIC", "SOLIDWORKS Premium - test frame", WS_OVERLAPPEDWINDOW,
            0, 0, 900, 700, None, None, None, None,
        )
        self._ready.set()
        try:
            self.result = _u32.MessageBoxW(self.frame, self.text, self.title, self.mb_type)
        finally:
            _u32.DestroyWindow(self.frame)

    def __enter__(self):
        self._thread.start()
        self._ready.wait(5)
        assert self.frame, "could not create the fake SolidWorks frame"
        deadline = time.time() + 5
        while time.time() < deadline and _u32.IsWindowEnabled(self.frame):
            time.sleep(0.05)  # the MessageBox disables its owner once it is up
        assert not _u32.IsWindowEnabled(self.frame), "MessageBox did not come up"
        return self

    def alive(self) -> bool:
        return self._thread.is_alive()

    def wait_closed(self, timeout: float = 3.0) -> bool:
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def __exit__(self, *exc):
        # Never leave a MessageBox on the desktop, whatever the test did.
        if self._thread.is_alive() and self.thread_id:
            def close(hwnd, _):
                _u32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
                return True
            _u32.EnumThreadWindows(self.thread_id, _ENUM_THREAD_WNDPROC(close), 0)
            self._thread.join(3)
        return False


@pytest.fixture(autouse=True)
def only_this_process(monkeypatch):
    """Point the dialog manager at this process, never at a real SLDWORKS.exe."""
    monkeypatch.setattr(dm, "get_sldworks_pids", lambda: [os.getpid()])


def _blocking(title):
    return [d for d in dm.list_active_dialogs() if d["title"] == title]


def test_blocking_dialog_is_found_with_text_and_buttons():
    with FakeSolidWorksModal("Sketch2: Overconstrained geometry", "SOLIDWORKS Error") as modal:
        dialogs = _blocking("SOLIDWORKS Error")
        assert len(dialogs) == 1, dm.list_active_dialogs()
        diag = dialogs[0]
        assert diag["hwnd"] != modal.frame, "the main frame must never be reported as a dialog"
        assert "Overconstrained" in " ".join(diag["messages"])
        assert {"OK", "Cancel"} <= {b["text"].replace("&", "") for b in diag["buttons"]}
        assert dm.find_blocking_modal_dialog()["hwnd"] == diag["hwnd"]


def test_dismiss_clicks_the_named_button():
    with FakeSolidWorksModal("Rebuild errors occurred.", "SOLIDWORKS Error") as modal:
        diag = _blocking("SOLIDWORKS Error")[0]
        res = dm.dismiss_dialog(diag["hwnd"], action="Cancel")
        assert res["success"], res
        assert res["button"]["text"].replace("&", "") == "Cancel"
        assert modal.wait_closed()
        assert modal.result == 2  # IDCANCEL


def test_dismiss_refuses_a_window_that_is_not_the_blocking_dialog():
    with FakeSolidWorksModal("Save changes?", "SOLIDWORKS") as modal:
        res = dm.dismiss_dialog(modal.frame, action="OK")
        assert not res["success"] and "Refusing" in res["message"]
        assert modal.alive(), "nothing may be clicked when the HWND is not the blocking dialog"


def test_dismiss_refuses_when_nothing_is_blocking():
    res = dm.dismiss_dialog(None, action="OK")
    assert not res["success"] and "No blocking modal dialog" in res["message"]


def test_auto_resolve_is_off_by_default(monkeypatch):
    monkeypatch.setattr(dm, "get_config", lambda: SimpleNamespace(auto_dismiss_known_dialogs=False))
    with FakeSolidWorksModal("Boss-Extrude1 failed.", "What's Wrong") as modal:
        assert dm.auto_resolve_blocking_dialogs() == []
        assert modal.alive()


def test_auto_resolve_closes_whats_wrong_when_enabled(monkeypatch):
    monkeypatch.setattr(dm, "get_config", lambda: SimpleNamespace(auto_dismiss_known_dialogs=True))
    with FakeSolidWorksModal("Boss-Extrude1 failed.", "What's Wrong") as modal:
        results = dm.auto_resolve_blocking_dialogs()
        assert len(results) == 1 and results[0]["title"] == "What's Wrong"
        assert modal.wait_closed()


def test_auto_resolve_leaves_unknown_prompts_alone(monkeypatch):
    monkeypatch.setattr(dm, "get_config", lambda: SimpleNamespace(auto_dismiss_known_dialogs=True))
    with FakeSolidWorksModal("Delete 3 features?", "SOLIDWORKS") as modal:
        assert dm.auto_resolve_blocking_dialogs() == []
        assert modal.alive()


def test_mcp_tool_definitions():
    names = {t.name for t in _tool_definitions()}
    assert {"inspect_dialogs", "dismiss_dialog", "load_addin", "inspect_rebuild_errors"} <= names


def test_inspect_dialogs_reports_the_fake_prompt(monkeypatch):
    # diagnostics.py imported list_active_dialogs by name; it calls get_sldworks_pids through dm.
    with FakeSolidWorksModal("Sketch3 is underdefined.", "SOLIDWORKS Warning"):
        res = sw_automation.inspect_dialogs()
        assert res["success"]
        titles = [d["title"] for d in res["data"]["dialogs"]]
        assert "SOLIDWORKS Warning" in titles


def test_inspect_rebuild_errors_shape(monkeypatch):
    """Runs on a fake document so the test needs no SolidWorks."""

    class Feature:
        def __init__(self, name, code=0, nxt=None):
            self.Name, self._code, self.GetNextFeature = name, code, nxt

        def GetTypeName2(self):
            return "Extrusion"

        def GetErrorCode2(self, is_warn):
            return self._code

    doc = SimpleNamespace(FirstFeature=Feature("Boss-Extrude1", 0, Feature("Cut-Extrude1", 7)))
    monkeypatch.setattr(sw_automation, "get_active_doc", lambda *a, **k: (doc, None))
    monkeypatch.setattr(sw_automation, "get_document_type", lambda d: "Part")

    res = sw_automation.inspect_rebuild_errors()
    assert res["success"], res
    data = res["data"]
    assert data["rebuild_clean"] is False and data["error_count"] == 1
    assert data["errors"][0]["feature_name"] == "Cut-Extrude1"
    assert data["warnings"] == []
