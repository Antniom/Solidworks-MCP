"""
Unit tests for silent startup auto-updater (solidworks_mcp.utils.auto_updater).
"""

import os
import subprocess
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
from solidworks_mcp.utils.auto_updater import check_and_apply_updates


def test_updater_bypassed_by_no_auto_update_env():
    """Verify SW_MCP_NO_AUTO_UPDATE=1 immediately returns False without running git."""
    with patch.dict(os.environ, {"SW_MCP_NO_AUTO_UPDATE": "1"}, clear=False):
        with patch("subprocess.run") as mock_run:
            result = check_and_apply_updates()
            assert result is False
            mock_run.assert_not_called()


def test_updater_bypassed_by_loop_guard():
    """Verify _SW_MCP_UPDATED=1 immediately returns False to prevent restart loops."""
    with patch.dict(os.environ, {"_SW_MCP_UPDATED": "1"}, clear=False):
        with patch("subprocess.run") as mock_run:
            result = check_and_apply_updates()
            assert result is False
            mock_run.assert_not_called()


def test_updater_skips_when_no_git_dir(tmp_path):
    """Verify updater cleanly exits when not running from a git repo (e.g. site-packages)."""
    with patch.dict(os.environ, {"SW_MCP_NO_AUTO_UPDATE": "0", "_SW_MCP_UPDATED": "0"}, clear=False):
        result = check_and_apply_updates(repo_root=tmp_path)
        assert result is False


def test_updater_skips_on_dirty_working_tree(tmp_path):
    """Verify uncommitted developer edits are protected and auto-pull is skipped."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()

    dirty_mock = MagicMock(returncode=0, stdout=" M solidworks_mcp/server.py\n")
    with patch.dict(os.environ, {"SW_MCP_NO_AUTO_UPDATE": "0", "_SW_MCP_UPDATED": "0"}, clear=False):
        with patch("subprocess.run", return_value=dirty_mock) as mock_run:
            result = check_and_apply_updates(repo_root=tmp_path)
            assert result is False
            assert mock_run.call_count == 1
            assert mock_run.call_args[0][0] == ["git", "status", "--porcelain"]


def test_updater_fail_open_on_timeout_zero_stdout(tmp_path, capsys):
    """Verify network timeout on git fetch fails open gracefully with ZERO stdout pollution."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()

    def mock_subprocess(cmd, **kwargs):
        if cmd == ["git", "status", "--porcelain"]:
            return MagicMock(returncode=0, stdout="")
        if cmd == ["git", "remote"]:
            return MagicMock(returncode=0, stdout="public\norigin\n")
        if "fetch" in cmd:
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=3.0)
        return MagicMock(returncode=0, stdout="")

    with patch.dict(os.environ, {"SW_MCP_NO_AUTO_UPDATE": "0", "_SW_MCP_UPDATED": "0"}, clear=False):
        with patch("subprocess.run", side_effect=mock_subprocess):
            result = check_and_apply_updates(repo_root=tmp_path)
            assert result is False

    captured = capsys.readouterr()
    # STRICT MCP INVARIANT: ZERO stdout pollution
    assert captured.out == "", "Updater must NEVER write to sys.stdout!"


def test_updater_triggers_pull_skill_sync_and_execv(tmp_path, capsys):
    """Verify full update flow: remote ahead -> git pull -> skills sync -> hot-reload."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()

    def mock_subprocess(cmd, **kwargs):
        if cmd == ["git", "status", "--porcelain"]:
            return MagicMock(returncode=0, stdout="")
        if cmd == ["git", "remote"]:
            return MagicMock(returncode=0, stdout="public\n")
        if cmd[:3] == ["git", "fetch", "public"]:
            return MagicMock(returncode=0, stdout="")
        if cmd == ["git", "rev-parse", "HEAD"]:
            return MagicMock(returncode=0, stdout="aaaaaaa\n")
        if cmd == ["git", "rev-parse", "public/main"]:
            return MagicMock(returncode=0, stdout="bbbbbbb\n")
        if cmd == ["git", "rev-list", "HEAD..public/main", "--count"]:
            return MagicMock(returncode=0, stdout="3\n")
        if cmd[:4] == ["git", "pull", "--ff-only", "public"]:
            return MagicMock(returncode=0, stdout="")
        return MagicMock(returncode=0, stdout="")

    mock_client = MagicMock()
    mock_client.is_installed.return_value = True
    mock_client.skills_dirs = [tmp_path / "skills"]

    with patch.dict(
        os.environ,
        {
            "SW_MCP_NO_AUTO_UPDATE": "0",
            "_SW_MCP_UPDATED": "0",
            "_SW_MCP_TEST_NO_EXECV": "1",
        },
        clear=False,
    ):
        with patch("subprocess.run", side_effect=mock_subprocess):
            with patch("solidworks_mcp.install.get_detected_clients", return_value=[mock_client]):
                with patch("solidworks_mcp.install.install_skills") as mock_install:
                    result = check_and_apply_updates(repo_root=tmp_path)
                    assert result is True
                    mock_install.assert_called_once()
                    assert os.environ.get("_SW_MCP_UPDATED") == "1"

    captured = capsys.readouterr()
    assert captured.out == "", "Updater must NEVER write to sys.stdout!"
