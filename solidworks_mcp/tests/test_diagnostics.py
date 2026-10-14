"""
Unit and Integration Tests for SolidWorks Diagnostics & Dialog Management
--------------------------------------------------------------------------
Tests:
1. Dialog inspection and button extraction
2. Out-of-band programmatic button click (BM_CLICK / WM_COMMAND)
3. Auto-resolve of blocking dialogs
4. Standalone CLI dialog handler
5. SolidWorksAutomation mixin methods
"""

import sys
import time
import threading
import unittest
import ctypes
from pathlib import Path

# Add project root
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from solidworks_mcp.utils.dialog_manager import (
    get_all_solidworks_windows,
    list_active_dialogs,
    dismiss_dialog,
    auto_resolve_blocking_dialogs,
    inspect_child_controls,
)
from solidworks_mcp.server import sw_automation, _tool_definitions

user32 = ctypes.windll.user32


class TestDialogManager(unittest.TestCase):

    def test_dialog_inspection_and_dismiss_ok(self):
        """Test discovering a modal popup, reading messages, and clicking OK."""
        def show_dialog():
            user32.MessageBoxW(0, "Test error in Sketch2: Overconstrained geometry", "SolidWorks Error", 1)

        t = threading.Thread(target=show_dialog)
        t.start()
        time.sleep(0.5)

        try:
            dialogs = [w for w in get_all_solidworks_windows() if "SolidWorks Error" in w.get("title", "")]
            self.assertTrue(len(dialogs) > 0, "Failed to find 'SolidWorks Error' dialog")
            diag = dialogs[0]

            self.assertIn("Overconstrained", " ".join(diag.get("messages", [])))
            button_texts = [b["text"] for b in diag.get("buttons", [])]
            self.assertIn("OK", button_texts)
            self.assertIn("Cancel", button_texts)

            # Click OK
            res = dismiss_dialog(diag["hwnd"], action="OK")
            self.assertTrue(res.get("success"), f"Dismiss failed: {res}")
            self.assertEqual(res.get("button", {}).get("text"), "OK")
        finally:
            t.join(timeout=2.0)
            self.assertFalse(t.is_alive(), "Dialog thread should have terminated")

    def test_auto_resolve_whats_wrong(self):
        """Test auto-resolving What's Wrong dialog."""
        def show_whats_wrong():
            user32.MessageBoxW(0, "What's Wrong: Boss-Extrude1 failed.", "SolidWorks What's Wrong", 0)

        t = threading.Thread(target=show_whats_wrong)
        t.start()
        time.sleep(0.5)

        try:
            results = auto_resolve_blocking_dialogs()
            self.assertTrue(len(results) > 0, "auto_resolve should have dismissed What's Wrong")
            self.assertIn("What's Wrong", results[0]["title"])
        finally:
            t.join(timeout=2.0)
            self.assertFalse(t.is_alive(), "Dialog thread should have terminated")

    def test_mcp_tool_definitions(self):
        """Verify all 4 new diagnostic tools are registered in server."""
        names = [t.name for t in _tool_definitions()]
        for req in ["inspect_dialogs", "dismiss_dialog", "load_addin", "inspect_rebuild_errors"]:
            self.assertIn(req, names, f"Tool {req} not found in _tool_definitions()")

    def test_automation_mixin_methods(self):
        """Verify DiagnosticOperations mixin methods on sw_automation."""
        res_diag = sw_automation.inspect_dialogs()
        self.assertTrue(res_diag.get("success"))
        self.assertIn("dialogs", res_diag.get("data", {}))

        res_rebuild = sw_automation.inspect_rebuild_errors()
        self.assertIn("success", res_rebuild)
        self.assertIn("data", res_rebuild)
        self.assertIn("rebuild_clean", res_rebuild["data"])
        self.assertIn("errors", res_rebuild["data"])
        self.assertIn("warnings", res_rebuild["data"])

        res_addin = sw_automation.load_addin("simulation")
        self.assertIn("success", res_addin)


if __name__ == "__main__":
    unittest.main()
