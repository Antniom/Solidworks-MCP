"""
SolidWorks MCP Multi-Client Auto-Installer
-----------------------------------------
Auto-detects installed AI agents and IDEs on the user's Windows system,
registers the SolidWorks MCP server in their respective configuration files,
and installs all bundled SolidWorks CAD/FEA/Drafting skills.

Supported Clients:
- Claude Desktop
- Claude Code
- Antigravity / Gemini CLI
- Cursor
- Windsurf (Codeium)
- Codex / OpenAI CLI
- VS Code / GitHub Copilot
"""

import os
import sys
import json
import shutil
import logging
import argparse
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("solidworks_mcp.install")


def get_bundled_skills_dir() -> Path:
    """Locate the bundled skills directory inside the solidworks_mcp package."""
    pkg_dir = Path(__file__).resolve().parent
    bundled = pkg_dir / "skills"
    if bundled.exists() and bundled.is_dir():
        return bundled
    # Fallback to workspace root 'skills' directory if running in-tree
    workspace_skills = pkg_dir.parent / "skills"
    if workspace_skills.exists() and workspace_skills.is_dir():
        return workspace_skills
    return bundled


class ClientTarget:
    """Represents a supported AI assistant / MCP client."""

    def __init__(
        self,
        name: str,
        config_path: Path,
        config_format: str = "json_mcpServers",
        skills_dirs: Optional[List[Path]] = None,
        detection_hint: Optional[Path] = None,
        extra_config_paths: Optional[List[Path]] = None,
        mcp_dir: Optional[Path] = None,
    ):
        self.name = name
        self.config_path = config_path
        self.config_format = config_format
        self.skills_dirs = skills_dirs or []
        self.detection_hint = detection_hint or config_path.parent
        self.extra_config_paths = extra_config_paths or []
        self.mcp_dir = mcp_dir

    def is_installed(self) -> bool:
        """Check whether the client or its parent application directory exists on the system."""
        return self.config_path.exists() or (self.detection_hint and self.detection_hint.exists())


def get_detected_clients() -> List[ClientTarget]:
    """Inspect standard Windows filesystem paths to detect installed AI agents."""
    user_profile = Path(os.environ.get("USERPROFILE", os.path.expanduser("~")))
    app_data = Path(os.environ.get("APPDATA", user_profile / "AppData" / "Roaming"))
    local_app_data = Path(os.environ.get("LOCALAPPDATA", user_profile / "AppData" / "Local"))

    clients = [
        ClientTarget(
            name="Claude Desktop",
            config_path=app_data / "Claude" / "claude_desktop_config.json",
            config_format="json_mcpServers",
            skills_dirs=[],
            detection_hint=app_data / "Claude",
        ),
        ClientTarget(
            name="Claude Code",
            config_path=user_profile / ".claude.json",
            config_format="json_mcpServers",
            skills_dirs=[user_profile / ".claude" / "skills"],
            detection_hint=user_profile / ".claude",
            extra_config_paths=[user_profile / ".claude" / "mcp.json"],
        ),
        ClientTarget(
            name="Antigravity / Gemini",
            config_path=user_profile / ".gemini" / "config" / "mcp_config.json",
            config_format="json_mcpServers",
            skills_dirs=[
                user_profile / ".gemini" / "config" / "skills",
                user_profile / ".gemini" / "antigravity" / "skills",
            ],
            detection_hint=user_profile / ".gemini",
            extra_config_paths=[
                user_profile / ".gemini" / "antigravity" / "mcp_config.json",
            ],
            mcp_dir=user_profile / ".gemini" / "antigravity" / "mcp" / "solidworks",
        ),
        ClientTarget(
            name="Cursor",
            config_path=user_profile / ".cursor" / "mcp.json",
            config_format="json_mcpServers",
            skills_dirs=[user_profile / ".cursor" / "skills"],
            detection_hint=user_profile / ".cursor",
            extra_config_paths=[
                app_data / "Cursor" / "User" / "globalStorage" / "rooveterinaryinc.cursor-mcp" / "mcp.json"
            ],
        ),
        ClientTarget(
            name="Windsurf",
            config_path=user_profile / ".codeium" / "windsurf" / "mcp_config.json",
            config_format="json_mcpServers",
            skills_dirs=[user_profile / ".codeium" / "windsurf" / "skills"],
            detection_hint=user_profile / ".codeium" / "windsurf",
        ),
        ClientTarget(
            name="Codex CLI",
            config_path=user_profile / ".codex" / "mcp.json",
            config_format="json_mcpServers",
            skills_dirs=[user_profile / ".codex" / "skills"],
            detection_hint=user_profile / ".codex",
            extra_config_paths=[user_profile / ".codex" / "config.json"],
        ),
        ClientTarget(
            name="VS Code / GitHub Copilot",
            config_path=app_data / "Code" / "User" / "mcp.json",
            config_format="json_mcpServers",
            skills_dirs=[],
            detection_hint=app_data / "Code",
        ),
    ]

    return clients


def _update_single_config(
    target_path: Path,
    server_key: str,
    server_entry: Dict[str, Any],
    dry_run: bool = False,
) -> bool:
    """Safely updates or creates an mcpServers configuration JSON file."""
    config_data: Dict[str, Any] = {}
    if target_path.exists():
        try:
            with open(target_path, "r", encoding="utf-8-sig") as f:
                config_data = json.load(f)
        except Exception as e:
            logger.warning(f"Could not parse existing {target_path}: {e}. Creating fresh config.")
            config_data = {}

    if "mcpServers" not in config_data:
        config_data["mcpServers"] = {}

    config_data["mcpServers"][server_key] = server_entry

    if dry_run:
        logger.info(f"[DRY-RUN] Would update config at: {target_path}")
        return True

    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"Failed to write configuration to {target_path}: {e}")
        return False


def export_antigravity_schemas(mcp_dir: Path, dry_run: bool = False) -> int:
    """Exports tool schemas into Antigravity MCP directory for instant discovery."""
    import asyncio
    try:
        from solidworks_mcp.server import list_tools
        res = asyncio.run(list_tools(None, None))
        tools = res.tools if hasattr(res, "tools") else []
    except Exception as e:
        logger.warning(f"Could not load tool schemas for export: {e}")
        return 0

    if dry_run:
        logger.info(f"[DRY-RUN] Would export {len(tools)} schemas to {mcp_dir}")
        return len(tools)

    try:
        mcp_dir.mkdir(parents=True, exist_ok=True)
        for t in tools:
            params = getattr(t, "input_schema", getattr(t, "inputSchema", {}))
            tool_data = {
                "name": t.name,
                "description": t.description or "",
                "parameters": params,
            }
            tool_file = mcp_dir / f"{t.name}.json"
            with open(tool_file, "w", encoding="utf-8") as f:
                json.dump(tool_data, f, indent=2)

        instructions_path = mcp_dir / "instructions.md"
        instructions_content = (
            "MCP server for parametric 3D modeling, assemblies, drawings, and FEA simulation in Dassault Systèmes SolidWorks.\n"
            "All dimensions are in millimeters. Typical workflow: connect -> create_part / open_document -> create_sketch -> "
            "draw geometry -> extrude_sketch / cut_extrude / revolve_sketch -> save_document.\n"
            "Use select_by_geometry for declarative face/edge selection. Custom macros via execute_python require user consent or SW_MCP_ALLOW_EXEC=1.\n"
        )
        with open(instructions_path, "w", encoding="utf-8") as f:
            f.write(instructions_content)

        logger.info(f"Exported {len(tools)} tool schemas and instructions to: {mcp_dir}")
        return len(tools)
    except Exception as e:
        logger.warning(f"Failed exporting tool schemas to {mcp_dir}: {e}")
        return 0


def register_mcp_server(
    client: ClientTarget,
    python_exe: str,
    allow_exec: bool = False,
    dry_run: bool = False,
) -> bool:
    """
    Safely merges the solidworks MCP server configuration into client's config file
    without destroying existing entries.
    """
    server_key = "solidworks"
    env_vars: Dict[str, str] = {}
    if allow_exec:
        env_vars["SW_MCP_ALLOW_EXEC"] = "1"

    server_entry: Dict[str, Any] = {
        "command": python_exe,
        "args": ["-m", "solidworks_mcp.server"],
    }
    if env_vars:
        server_entry["env"] = env_vars

    primary_ok = _update_single_config(client.config_path, server_key, server_entry, dry_run=dry_run)
    if primary_ok:
        logger.info(f"Registered SolidWorks MCP in {client.name} ({client.config_path})")

    for extra_path in client.extra_config_paths:
        _update_single_config(extra_path, server_key, server_entry, dry_run=dry_run)

    if client.mcp_dir:
        export_antigravity_schemas(client.mcp_dir, dry_run=dry_run)

    return primary_ok


def _force_remove_readonly(func, path, exc_info):
    """Error handler for shutil.rmtree to unset read-only permissions on Windows."""
    import stat
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass


def install_skills(client: ClientTarget, bundled_skills: Path, dry_run: bool = False) -> int:
    """Installs all bundled SolidWorks skills into the client's skill directories."""
    if not bundled_skills.exists():
        logger.warning(f"Bundled skills directory not found: {bundled_skills}")
        return 0

    installed_count = 0
    for target_skills_dir in client.skills_dirs:
        if dry_run:
            logger.info(f"[DRY-RUN] Would copy bundled skills to {target_skills_dir}")
            continue

        try:
            target_skills_dir.mkdir(parents=True, exist_ok=True)
            for skill_folder in bundled_skills.iterdir():
                if skill_folder.is_dir() and (skill_folder / "SKILL.md").exists():
                    dest = target_skills_dir / skill_folder.name
                    if dest.exists():
                        shutil.rmtree(dest, onerror=_force_remove_readonly)
                    shutil.copytree(skill_folder, dest)
                    installed_count += 1
            logger.info(f"Installed {installed_count} SolidWorks skills into: {target_skills_dir}")
        except Exception as e:
            logger.warning(f"Failed copying skills into {target_skills_dir}: {e}")

    return installed_count



def install_antigravity_plugin(
    python_exe: str,
    target_dir: Optional[Path] = None,
    allow_exec: bool = False,
    dry_run: bool = False,
) -> bool:
    """
    Package and install the unified SolidWorks Antigravity plugin into
    ~/.gemini/config/plugins/solidworks.
    Bundles plugin.json, logo.svg, rules/AGENTS.md, mcp_config.json, and all skills.
    """
    pkg_root = Path(__file__).resolve().parent.parent
    user_profile = Path(os.environ.get("USERPROFILE", os.path.expanduser("~")))
    dest_plugin = target_dir or (user_profile / ".gemini" / "config" / "plugins" / "solidworks")
    repo_plugin = pkg_root / "plugins" / "solidworks"

    if dry_run:
        logger.info(f"[DRY-RUN] Would install Antigravity plugin to: {dest_plugin}")
        return True

    try:
        bundled_skills = get_bundled_skills_dir()
        
        # Ensure workspace repo plugin is synced first
        repo_plugin.mkdir(parents=True, exist_ok=True)
        repo_skills = repo_plugin / "skills"
        repo_skills.mkdir(parents=True, exist_ok=True)
        if bundled_skills.exists():
            for s in bundled_skills.iterdir():
                if s.is_dir() and (s / "SKILL.md").exists():
                    d = repo_skills / s.name
                    if d.exists():
                        shutil.rmtree(d, onerror=_force_remove_readonly)
                    shutil.copytree(s, d)

        # Build mcp_config.json content
        env_vars: Dict[str, str] = {
            "PYTHONPATH": str(pkg_root),
        }
        if allow_exec:
            env_vars["SW_MCP_ALLOW_EXEC"] = "1"

        mcp_config = {
            "mcpServers": {
                "solidworks": {
                    "command": python_exe,
                    "args": ["-m", "solidworks_mcp.server"],
                    "env": env_vars,
                }
            }
        }

        # Write to repo_plugin/mcp_config.json
        with open(repo_plugin / "mcp_config.json", "w", encoding="utf-8") as f:
            json.dump(mcp_config, f, indent=2)

        # Now stage to destination plugin directory
        dest_plugin.mkdir(parents=True, exist_ok=True)
        
        # Copy plugin.json
        if (repo_plugin / "plugin.json").exists():
            shutil.copy2(repo_plugin / "plugin.json", dest_plugin / "plugin.json")

        # Copy mcp_config.json
        with open(dest_plugin / "mcp_config.json", "w", encoding="utf-8") as f:
            json.dump(mcp_config, f, indent=2)

        # Copy assets
        if (repo_plugin / "assets").exists():
            dest_assets = dest_plugin / "assets"
            dest_assets.mkdir(parents=True, exist_ok=True)
            for a in (repo_plugin / "assets").iterdir():
                shutil.copy2(a, dest_assets / a.name)

        # Copy rules
        if (repo_plugin / "rules").exists():
            dest_rules = dest_plugin / "rules"
            dest_rules.mkdir(parents=True, exist_ok=True)
            for r in (repo_plugin / "rules").iterdir():
                shutil.copy2(r, dest_rules / r.name)

        # Copy skills
        dest_skills = dest_plugin / "skills"
        dest_skills.mkdir(parents=True, exist_ok=True)
        if repo_skills.exists():
            for s in repo_skills.iterdir():
                if s.is_dir() and (s / "SKILL.md").exists():
                    d = dest_skills / s.name
                    if d.exists():
                        shutil.rmtree(d, onerror=_force_remove_readonly)
                    shutil.copytree(s, d)

        logger.info(f"Successfully packaged and installed SolidWorks plugin at: {dest_plugin}")
        return True
    except Exception as e:
        logger.error(f"Failed installing Antigravity plugin: {e}")
        return False


def prompt_execute_python_consent() -> bool:
    """Interactive CLI prompt explaining benefits and security risks of execute_python."""
    print("\n" + "=" * 76)
    print(" [!] SECURITY GATING CONFIGURATION: `execute_python`")
    print("=" * 76)
    print(
        "The `execute_python` tool allows the AI assistant to run arbitrary Python code\n"
        "directly on your Windows system with access to the SolidWorks COM API.\n\n"
        "BENEFITS:\n"
        "- Full access to `sw_app`, `model`, and `SelectionMgr` for custom macros.\n"
        "- Batch geometry calculations and specialized COM methods not in standard tools.\n\n"
        "RISKS:\n"
        "- Arbitrary code execution running with your current Windows user privileges.\n"
        "- Bypasses filesystem policy and tool validation safeguards.\n\n"
        "RECOMMENDATION:\n"
        "Keep disabled by default (N). When needed in chat, the AI will prompt you for\n"
        "one-time explicit consent before executing any Python script.\n"
    )
    choice = input("Do you want to enable SW_MCP_ALLOW_EXEC=1 permanently in client configs? (y/N): ").strip().lower()
    return choice in ("y", "yes", "1")


def run_installer(
    python_exe: Optional[str] = None,
    allow_exec: Optional[bool] = None,
    client_name: Optional[str] = None,
    dry_run: bool = False,
    non_interactive: bool = False,
) -> int:
    """Execute full multi-client installation workflow."""
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    print("=" * 76)
    print(" [*] SOLIDWORKS MCP MULTI-CLIENT AUTO-INSTALLER")
    print("=" * 76)

    if not python_exe:
        pkg_root = Path(__file__).resolve().parent.parent
        local_venv_py = pkg_root / ".venv" / "Scripts" / "python.exe"
        cwd_venv_py = Path.cwd() / ".venv" / "Scripts" / "python.exe"
        if hasattr(sys, "real_prefix") or (hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix):
            resolved_python = sys.executable
        elif local_venv_py.exists():
            resolved_python = str(local_venv_py)
        elif cwd_venv_py.exists():
            resolved_python = str(cwd_venv_py)
        else:
            resolved_python = sys.executable
    else:
        resolved_python = python_exe
    logger.info(f"Target Python executable: {resolved_python}")

    bundled_skills = get_bundled_skills_dir()
    logger.info(f"Bundled skills source: {bundled_skills}")

    all_clients = get_detected_clients()
    detected = [c for c in all_clients if c.is_installed()]

    if not detected:
        logger.warning("No installed AI clients detected in standard locations.")
        print("Available detection paths checked:")
        for c in all_clients:
            print(f"  - {c.name}: {c.config_path}")
        # Default to installing for Claude Desktop and Antigravity
        detected = [c for c in all_clients if c.name in ("Claude Desktop", "Antigravity / Gemini")]

    print(f"\nDetected AI clients ready for MCP registration ({len(detected)}):")
    for c in detected:
        print(f"  [x] {c.name} (config: {c.config_path.name})")

    # Filter by specific client if requested
    if client_name:
        detected = [c for c in detected if client_name.lower() in c.name.lower()]
        if not detected:
            logger.error(f"Specified client '{client_name}' not found among detected targets.")
            return 1

    # Security consent for execute_python
    final_allow_exec = False
    if allow_exec is not None:
        final_allow_exec = allow_exec
    elif not non_interactive and sys.stdin.isatty():
        final_allow_exec = prompt_execute_python_consent()
    else:
        logger.info("Non-interactive mode: keeping SW_MCP_ALLOW_EXEC=0 (default secure gated state).")

    print("\nRegistering SolidWorks MCP and installing skills...")
    success_count = 0
    total_skills_installed = 0

    for client in detected:
        ok = register_mcp_server(
            client=client,
            python_exe=resolved_python,
            allow_exec=final_allow_exec,
            dry_run=dry_run,
        )
        if ok:
            success_count += 1
        n_skills = install_skills(client, bundled_skills, dry_run=dry_run)
        total_skills_installed += n_skills

    # Install unified Antigravity Plugin
    plugin_ok = install_antigravity_plugin(
        python_exe=resolved_python,
        allow_exec=final_allow_exec,
        dry_run=dry_run,
    )
    if plugin_ok:
        logger.info("Antigravity / Gemini unified SolidWorks plugin installed successfully.")

    print("\n" + "=" * 76)
    print(f" [OK] Installation complete: {success_count}/{len(detected)} clients configured.")
    print(f" [*] Bundled SolidWorks skills installed across {total_skills_installed} destinations.")
    print("=" * 76)
    print("To use: Restart your AI client(s) (Claude Desktop, Antigravity, Cursor, etc.).\n")
    return 0


def main():
    parser = argparse.ArgumentParser(description="SolidWorks MCP Auto-Installer for AI Clients")
    parser.add_argument("--python", type=str, default=None, help="Path to Python executable to use in MCP config")
    parser.add_argument("--allow-exec", action="store_true", default=None, help="Permanently enable SW_MCP_ALLOW_EXEC=1 in configs")
    parser.add_argument("--no-allow-exec", action="store_false", dest="allow_exec", help="Enforce SW_MCP_ALLOW_EXEC=0 in configs")
    parser.add_argument("--client", type=str, default=None, help="Target a specific client by name")
    parser.add_argument("--install-plugin", action="store_true", default=True, help="Package and install Antigravity plugin")
    parser.add_argument("--dry-run", action="store_true", help="Preview installation without modifying files")
    parser.add_argument("--non-interactive", action="store_true", help="Run without interactive prompts")

    args = parser.parse_args()
    ret = run_installer(
        python_exe=args.python,
        allow_exec=args.allow_exec,
        client_name=args.client,
        dry_run=args.dry_run,
        non_interactive=args.non_interactive,
    )
    sys.exit(ret)


if __name__ == "__main__":
    main()
