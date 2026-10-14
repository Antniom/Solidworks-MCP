"""
SolidWorks MCP Silent Startup Auto-Updater
------------------------------------------
Performs a fast, non-blocking check against the public repository on server startup.
If an update is available on a clean git working tree, fast-forwards the local clone,
synchronizes all bundled skills to detected AI clients, and hot-reloads the server.

STRICT INVARIANTS:
1. Zero stdout pollution: All subprocess stdout redirected to DEVNULL/PIPE; messages to sys.stderr.
2. Fast timeout (3.0s) and fail-open on network/git exceptions.
3. Skip check if _SW_MCP_UPDATED=1 or SW_MCP_NO_AUTO_UPDATE=1.
4. Dirty working tree protection: Never overwrite uncommitted local developer changes.
"""

import os
import sys
import subprocess
from pathlib import Path
from typing import Optional


def check_and_apply_updates(
    repo_root: Optional[Path] = None,
    timeout: float = 3.0,
) -> bool:
    """
    Check for remote updates and apply them cleanly if available.

    Returns:
        True if an update was applied and server is being hot-reloaded (or simulated).
        False if skipped, already up to date, offline, or on a dirty working tree.
    """
    # 1. Loop guard & user opt-out
    if os.environ.get("_SW_MCP_UPDATED") == "1":
        return False
    if os.environ.get("SW_MCP_NO_AUTO_UPDATE") == "1":
        return False

    try:
        # 2. Locate git repository root
        if repo_root is None:
            repo_root = Path(__file__).resolve().parent.parent.parent

        git_dir = repo_root / ".git"
        if not git_dir.exists():
            return False

        # 3. Check for dirty working tree (protect active local dev work)
        status_res = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(repo_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2.0,
        )
        if status_res.returncode != 0 or status_res.stdout.strip():
            # Dirty tree or status failure -> skip update
            return False

        # 4. Resolve upstream remote tracking public release
        remotes_res = subprocess.run(
            ["git", "remote"],
            cwd=str(repo_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2.0,
        )
        if remotes_res.returncode != 0:
            return False

        remotes = remotes_res.stdout.split()
        if "public" in remotes:
            remote = "public"
        elif "origin" in remotes:
            remote = "origin"
        elif remotes:
            remote = remotes[0]
        else:
            return False

        # 5. Fetch remote main with strict network timeout (fail-open)
        fetch_res = subprocess.run(
            ["git", "fetch", remote, "main", "--quiet"],
            cwd=str(repo_root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )
        if fetch_res.returncode != 0:
            return False

        # 6. Compare local HEAD SHA with remote main
        head_res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2.0,
        )
        remote_res = subprocess.run(
            ["git", "rev-parse", f"{remote}/main"],
            cwd=str(repo_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2.0,
        )
        local_sha = head_res.stdout.strip()
        remote_sha = remote_res.stdout.strip()

        if not local_sha or not remote_sha or local_sha == remote_sha:
            return False

        # Verify remote is strictly ahead
        count_res = subprocess.run(
            ["git", "rev-list", f"HEAD..{remote}/main", "--count"],
            cwd=str(repo_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2.0,
        )
        count_str = count_res.stdout.strip()
        if not count_str.isdigit() or int(count_str) <= 0:
            return False

        # 7. Fast-forward pull
        pull_res = subprocess.run(
            ["git", "pull", "--ff-only", remote, "main", "--quiet"],
            cwd=str(repo_root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10.0,
        )
        if pull_res.returncode != 0:
            return False

        # 8. Synchronize updated skills to all detected AI clients
        try:
            from solidworks_mcp.install import (
                get_detected_clients,
                install_skills,
                get_bundled_skills_dir,
            )
            bundled = get_bundled_skills_dir()
            for client in get_detected_clients():
                if client.is_installed() and client.skills_dirs:
                    install_skills(client, bundled)
        except Exception as sync_err:
            sys.stderr.write(f"[solidworks_mcp] Skill sync warning during auto-update: {sync_err}\n")

        # 9. Hot-reload server with loop guard
        os.environ["_SW_MCP_UPDATED"] = "1"
        sys.stderr.write(
            f"[solidworks_mcp] Updated to latest release ({local_sha[:7]} -> {remote_sha[:7]}). Hot-reloading...\n"
        )
        sys.stderr.flush()

        # In testing environments, don't execute execv if flag set
        if os.environ.get("_SW_MCP_TEST_NO_EXECV") == "1":
            return True

        os.execv(sys.executable, [sys.executable] + sys.argv)
        return True

    except Exception as exc:
        # Fail-open: write brief diagnostic to stderr and proceed with current local code
        sys.stderr.write(f"[solidworks_mcp] Auto-updater skipped ({exc})\n")
        return False
