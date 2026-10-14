"""
Unit test for B2 dialog allowlist matching with real dialog data.
Real Dialog Data:
  Title: "SOLIDWORKS"
  Buttons: [
      "&Yes, enable 3DEXPERIENCE Marketplace.",
      "&No, disable 3DEXPERIENCE Marketplace."
  ]
"""

import unittest
from unittest.mock import patch, MagicMock
from solidworks_mcp.config import SolidWorksConfig, get_config
from solidworks_mcp.utils.dialog_manager import auto_resolve_blocking_dialogs


class TestB2DialogAllowlist(unittest.TestCase):
    def setUp(self):
        self.real_dialog_data = {
            "hwnd": 12345,
            "title": "SOLIDWORKS",
            "messages": ["Would you like to enable 3DEXPERIENCE Marketplace?"],
            "buttons": [
                {"hwnd": 12346, "id": 1, "text": "&Yes, enable 3DEXPERIENCE Marketplace."},
                {"hwnd": 12347, "id": 2, "text": "&No, disable 3DEXPERIENCE Marketplace."},
            ]
        }

    def test_old_check_logic_evaluation(self):
        """
        Verify whether the old check 'any(\"3dexperience marketplace\" in b for b in btn_texts)'
        matched on the real dialog buttons.
        """
        buttons = self.real_dialog_data["buttons"]
        btn_texts = {b.get("text", "").strip().lower() for b in buttons}
        # btn_texts is {"&yes, enable 3dexperience marketplace.", "&no, disable 3dexperience marketplace."}
        old_check_matched = any("3dexperience marketplace" in b for b in btn_texts)
        # Because the button text itself contains the substring '3dexperience marketplace',
        # the old check DID MATCH.
        self.assertTrue(
            old_check_matched,
            "Old check matched because button text literally contains '3dexperience marketplace'."
        )

    @patch("solidworks_mcp.utils.dialog_manager.list_active_dialogs")
    @patch("solidworks_mcp.utils.dialog_manager.get_config")
    def test_policy_default_off_does_not_auto_resolve(self, mock_get_cfg, mock_list_dialogs):
        """
        Policy: auto-resolution OFF by default (config auto_dismiss_known_dialogs=false).
        Default behaviour: auto_resolve_blocking_dialogs returns [] (does not dismiss).
        Dialog remains open so swModalDialogBlocking is raised with title, buttons, hwnd.
        """
        mock_cfg = SolidWorksConfig(auto_dismiss_known_dialogs=False)
        mock_get_cfg.return_value = mock_cfg
        mock_list_dialogs.return_value = [self.real_dialog_data]

        results = auto_resolve_blocking_dialogs()
        self.assertEqual(results, [], "When auto_dismiss_known_dialogs=False, nothing must be auto-dismissed.")

    @patch("solidworks_mcp.utils.dialog_manager.dismiss_dialog")
    @patch("solidworks_mcp.utils.dialog_manager.list_active_dialogs")
    @patch("solidworks_mcp.utils.dialog_manager.get_config")
    def test_policy_enabled_exact_match_presses_no_disable(self, mock_get_cfg, mock_list_dialogs, mock_dismiss):
        """
        When auto_dismiss_known_dialogs=True:
        Must match by EXACT title ('SOLIDWORKS') AND exact button set,
        and press ONLY 'No, disable...' (which changes a SolidWorks setting).
        """
        mock_cfg = SolidWorksConfig(auto_dismiss_known_dialogs=True)
        mock_get_cfg.return_value = mock_cfg
        mock_list_dialogs.return_value = [self.real_dialog_data]
        mock_dismiss.return_value = {"success": True, "message": "Dismissed"}

        results = auto_resolve_blocking_dialogs()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "SOLIDWORKS")
        mock_dismiss.assert_called_once_with(12345, "No, disable")
        self.assertIn("changes a SolidWorks setting", results[0]["action"])

    @patch("solidworks_mcp.utils.dialog_manager.dismiss_dialog")
    @patch("solidworks_mcp.utils.dialog_manager.list_active_dialogs")
    @patch("solidworks_mcp.utils.dialog_manager.get_config")
    def test_policy_enabled_wrong_title_or_buttons_rejected(self, mock_get_cfg, mock_list_dialogs, mock_dismiss):
        """
        If title or button set is not exact, do NOT dismiss.
        """
        mock_cfg = SolidWorksConfig(auto_dismiss_known_dialogs=True)
        mock_get_cfg.return_value = mock_cfg

        # Case A: Different title
        diff_title_diag = dict(self.real_dialog_data)
        diff_title_diag["title"] = "Different Title"
        mock_list_dialogs.return_value = [diff_title_diag]

        results = auto_resolve_blocking_dialogs()
        self.assertEqual(results, [])
        mock_dismiss.assert_not_called()

        # Case B: Different button set (e.g. only OK/Cancel)
        diff_btns_diag = dict(self.real_dialog_data)
        diff_btns_diag["buttons"] = [{"hwnd": 1, "id": 1, "text": "OK"}, {"hwnd": 2, "id": 2, "text": "Cancel"}]
        mock_list_dialogs.return_value = [diff_btns_diag]

        results = auto_resolve_blocking_dialogs()
        self.assertEqual(results, [])
        mock_dismiss.assert_not_called()


if __name__ == "__main__":
    unittest.main()
