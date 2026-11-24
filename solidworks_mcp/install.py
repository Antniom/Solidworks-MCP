"""
SolidWorks MCP Multi-Client Auto-Installer
-----------------------------------------
Auto-detects installed AI agents and IDEs on the user's Windows system,
registers the SolidWorks MCP server in their respective configuration files,
and installs all bundled SolidWorks CAD/FEA/Drafting skills.

Supported Clients:
- Claude Desktop
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


_REPO_LAYOUT = {"skills": "skills", "rules": "rules", "plugin": "plugins/solidworks"}


def _resource_dir(name: str) -> Path:
    """
    Locate bundled, read-only resources ("skills", "rules", "plugin").
    A wheel carries them under solidworks_mcp/_bundle; a source checkout keeps them at the repo root.
    """
    pkg_dir = Path(__file__).resolve().parent
    bundled = pkg_dir / "_bundle" / name
    if bundled.is_dir():
        return bundled
    return pkg_dir.parent / _REPO_LAYOUT[name]


def get_bundled_skills_dir() -> Path:
    """Directory holding the SolidWorks skills shipped with this package."""
    return _resource_dir("skills")


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
        # "json_mcpServers" | "json_servers" (VS Code) | "toml_mcp_servers" (Codex)
        self.config_format = config_format
        self.skills_dirs = skills_dirs or []
        self.detection_hint = detection_hint or config_path.parent
        # Secondary config files, updated only when they already exist.
        self.extra_config_paths = extra_config_paths or []
        self.mcp_dir = mcp_dir

    def is_installed(self) -> bool:
        """Check whether the client or its parent application directory exists on the system."""
        return self.config_path.exists() or bool(self.detection_hint and self.detection_hint.exists())


def get_detected_clients() -> List[ClientTarget]:
    """All supported clients with their standard Windows config locations."""
    user_profile = Path(os.environ.get("USERPROFILE", os.path.expanduser("~")))
    app_data = Path(os.environ.get("APPDATA", user_profile / "AppData" / "Roaming"))

    return [
        ClientTarget(
            name="Claude Desktop",
            config_path=app_data / "Claude" / "claude_desktop_config.json",
            detection_hint=app_data / "Claude",
        ),
        ClientTarget(
            name="Antigravity / Gemini",
            config_path=user_profile / ".gemini" / "config" / "mcp_config.json",
            skills_dirs=[
                user_profile / ".gemini" / "config" / "skills",
                user_profile / ".gemini" / "antigravity" / "skills",
            ],
            detection_hint=user_profile / ".gemini",
            extra_config_paths=[user_profile / ".gemini" / "antigravity" / "mcp_config.json"],
            mcp_dir=user_profile / ".gemini" / "antigravity" / "mcp" / "solidworks",
        ),
        ClientTarget(
            name="Cursor",
            config_path=user_profile / ".cursor" / "mcp.json",
            skills_dirs=[user_profile / ".cursor" / "skills"],
            detection_hint=user_profile / ".cursor",
        ),
        ClientTarget(
            name="Windsurf",
            config_path=user_profile / ".codeium" / "windsurf" / "mcp_config.json",
            skills_dirs=[user_profile / ".codeium" / "windsurf" / "skills"],
            detection_hint=user_profile / ".codeium" / "windsurf",
        ),
        ClientTarget(
            name="Codex CLI",
            config_path=user_profile / ".codex" / "config.toml",
            config_format="toml_mcp_servers",
            skills_dirs=[user_profile / ".codex" / "skills"],
            detection_hint=user_profile / ".codex",
        ),
        ClientTarget(
            name="VS Code / GitHub Copilot",
            config_path=app_data / "Code" / "User" / "mcp.json",
            config_format="json_servers",
            detection_hint=app_data / "Code",
        ),
    ]


def _write_text_safely(target_path: Path, text: str) -> None:
    """Back up the existing file, then replace it atomically."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists():
        shutil.copy2(target_path, target_path.with_name(target_path.name + ".solidworks-mcp.bak"))
    tmp_path = target_path.with_name(target_path.name + ".solidworks-mcp.tmp")
    with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp_path, target_path)


def _update_single_config(
    target_path: Path,
    server_key: str,
    server_entry: Dict[str, Any],
    dry_run: bool = False,
    root_key: str = "mcpServers",
) -> bool:
    """
    Merge one server entry into a JSON config file, leaving everything else untouched.

    A file that exists but cannot be parsed is never overwritten: these files hold the
    user's other servers and, for some clients, all of their application state.
    """
    config_data: Dict[str, Any] = {}
    if target_path.exists():
        try:
            with open(target_path, "r", encoding="utf-8-sig") as f:
                raw = f.read()
            config_data = json.loads(raw) if raw.strip() else {}
        except Exception as e:
            logger.error(f"Skipping {target_path}: existing file is not valid JSON ({e}). Fix it and re-run.")
            return False
        if not isinstance(config_data, dict) or not isinstance(config_data.get(root_key, {}), dict):
            logger.error(f"Skipping {target_path}: unexpected structure (expected an object with '{root_key}').")
            return False

    if config_data.get(root_key, {}).get(server_key) == server_entry:
        return True
    config_data.setdefault(root_key, {})[server_key] = server_entry

    if dry_run:
        logger.info(f"[DRY-RUN] Would update config at: {target_path}")
        return True
    try:
        _write_text_safely(target_path, json.dumps(config_data, indent=2) + "\n")
        return True
    except Exception as e:
        logger.error(f"Failed to write configuration to {target_path}: {e}")
        return False


def _toml_server_block(server_key: str, server_entry: Dict[str, Any]) -> str:
    # JSON string/array syntax is valid TOML for these value types.
    lines = [
        f"[mcp_servers.{server_key}]",
        f"command = {json.dumps(server_entry['command'])}",
        f"args = {json.dumps(server_entry.get('args', []))}",
    ]
    env = server_entry.get("env") or {}
    if env:
        lines += ["", f"[mcp_servers.{server_key}.env]"]
        lines += [f"{k} = {json.dumps(str(v))}" for k, v in env.items()]
    return "\n".join(lines) + "\n"


def _update_toml_config(
    target_path: Path,
    server_key: str,
    server_entry: Dict[str, Any],
    dry_run: bool = False,
) -> bool:
    """Replace (or append) the [mcp_servers.<key>] tables in a Codex config.toml, keeping the rest verbatim."""
    existing = ""
    if target_path.exists():
        try:
            existing = target_path.read_text(encoding="utf-8-sig")
        except Exception as e:
            logger.error(f"Skipping {target_path}: cannot read it ({e}).")
            return False

    own_header = f"[mcp_servers.{server_key}"
    kept: List[str] = []
    skipping = False
    for line in existing.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            skipping = stripped.startswith(own_header + "]") or stripped.startswith(own_header + ".")
        if not skipping:
            kept.append(line)
    body = "\n".join(kept).rstrip()
    new_text = (body + "\n\n" if body else "") + _toml_server_block(server_key, server_entry)

    if new_text == existing:
        return True
    if dry_run:
        logger.info(f"[DRY-RUN] Would update config at: {target_path}")
        return True
    try:
        _write_text_safely(target_path, new_text)
        return True
    except Exception as e:
        logger.error(f"Failed to write configuration to {target_path}: {e}")
        return False


def export_antigravity_schemas(mcp_dir: Path, dry_run: bool = False) -> int:
    """Exports tool schemas into Antigravity MCP directory for instant discovery."""
    try:
        from solidworks_mcp.tool_schemas import tool_definitions
        tools = tool_definitions()
    except Exception as e:
        logger.warning(f"Could not load tool schemas for export: {e}")
        return 0

    if dry_run:
        logger.info(f"[DRY-RUN] Would export {len(tools)} schemas to {mcp_dir}")
        return len(tools)

    try:
        mcp_dir.mkdir(parents=True, exist_ok=True)
        for t in tools:
            tool_data = {"name": t.name, "description": t.description or "", "parameters": t.input_schema}
            with open(mcp_dir / f"{t.name}.json", "w", encoding="utf-8") as f:
                json.dump(tool_data, f, indent=2)

        instructions_content = (
            "MCP server for parametric 3D modeling, assemblies, drawings, and FEA simulation in Dassault Systèmes SolidWorks.\n"
            "Lengths use the session unit (mm unless changed with set_units). Typical workflow: connect_solidworks -> "
            "create_document / open_document -> create_sketch -> draw geometry -> extrude_sketch / cut_extrude / "
            "revolve_sketch -> save_document.\n"
            "Use select with by=\"geometry\" for declarative face/edge selection. execute_code requires user "
            "consent or SW_MCP_ALLOW_EXEC=1.\n"
        )
        with open(mcp_dir / "instructions.md", "w", encoding="utf-8") as f:
            f.write(instructions_content)
        logger.info(f"Exported {len(tools)} tool schemas and instructions to: {mcp_dir}")
        return len(tools)
    except Exception as e:
        logger.warning(f"Failed exporting tool schemas to {mcp_dir}: {e}")
        return 0


def _server_entry(python_exe: str, allow_exec: bool, config_format: str = "json_mcpServers") -> Dict[str, Any]:
    entry: Dict[str, Any] = {"command": python_exe, "args": ["-m", "solidworks_mcp.server"]}
    if config_format == "json_servers":
        entry = {"type": "stdio", **entry}
    env: Dict[str, str] = {}
    # A source checkout is not on sys.path of the client's Python unless we say so.
    pkg_root = Path(__file__).resolve().parent.parent
    if (pkg_root / "pyproject.toml").exists():
        env["PYTHONPATH"] = str(pkg_root)
    if allow_exec:
        env["SW_MCP_ALLOW_EXEC"] = "1"
    if env:
        entry["env"] = env
    return entry


def register_mcp_server(
    client: ClientTarget,
    python_exe: str,
    allow_exec: bool = False,
    dry_run: bool = False,
) -> bool:
    """Merge the solidworks MCP server into the client's config without touching other entries."""
    server_key = "solidworks"
    entry = _server_entry(python_exe, allow_exec, client.config_format)

    if client.config_format == "toml_mcp_servers":
        primary_ok = _update_toml_config(client.config_path, server_key, entry, dry_run=dry_run)
    else:
        root_key = "servers" if client.config_format == "json_servers" else "mcpServers"
        primary_ok = _update_single_config(client.config_path, server_key, entry, dry_run=dry_run, root_key=root_key)
        for extra_path in client.extra_config_paths:
            if extra_path.exists():
                _update_single_config(extra_path, server_key, entry, dry_run=dry_run, root_key=root_key)

    if primary_ok:
        logger.info(f"Registered SolidWorks MCP in {client.name} ({client.config_path})")
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


def _copy_skills(source: Path, dest: Path) -> int:
    """Copy every skill folder from source into dest, replacing same-named folders. Returns the count."""
    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    for skill_folder in sorted(source.iterdir()):
        if skill_folder.is_dir() and (skill_folder / "SKILL.md").exists():
            target = dest / skill_folder.name
            if target.exists():
                shutil.rmtree(target, onerror=_force_remove_readonly)
            shutil.copytree(skill_folder, target)
            count += 1
    return count


def install_skills(client: ClientTarget, bundled_skills: Path, dry_run: bool = False) -> int:
    """Install the bundled skills into each of the client's skill directories. Returns copies made."""
    if not bundled_skills.exists():
        logger.warning(f"Bundled skills directory not found: {bundled_skills}")
        return 0

    installed_count = 0
    for target_skills_dir in client.skills_dirs:
        if dry_run:
            logger.info(f"[DRY-RUN] Would copy bundled skills to {target_skills_dir}")
            continue
        try:
            copied = _copy_skills(bundled_skills, target_skills_dir)
            installed_count += copied
            logger.info(f"Installed {copied} SolidWorks skills into: {target_skills_dir}")
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
    Stage the SolidWorks Antigravity plugin (plugin.json, assets, rules, skills, mcp_config.json)
    into ~/.gemini/config/plugins/solidworks. Only the destination is written.
    """
    user_profile = Path(os.environ.get("USERPROFILE", os.path.expanduser("~")))
    dest_plugin = target_dir or (user_profile / ".gemini" / "config" / "plugins" / "solidworks")
    plugin_src = _resource_dir("plugin")

    if dry_run:
        logger.info(f"[DRY-RUN] Would install Antigravity plugin to: {dest_plugin}")
        return True

    try:
        if not (plugin_src / "plugin.json").exists():
            raise FileNotFoundError(f"plugin.json not found in {plugin_src}")
        dest_plugin.mkdir(parents=True, exist_ok=True)
        shutil.copy2(plugin_src / "plugin.json", dest_plugin / "plugin.json")
        for name, src in (("assets", plugin_src / "assets"), ("rules", _resource_dir("rules"))):
            if src.is_dir():
                shutil.copytree(src, dest_plugin / name, dirs_exist_ok=True)
        _copy_skills(get_bundled_skills_dir(), dest_plugin / "skills")

        mcp_config = {"mcpServers": {"solidworks": _server_entry(python_exe, allow_exec)}}
        with open(dest_plugin / "mcp_config.json", "w", encoding="utf-8") as f:
            json.dump(mcp_config, f, indent=2)

        logger.info(f"Installed SolidWorks plugin at: {dest_plugin}")
        return True
    except Exception as e:
        logger.error(f"Failed installing Antigravity plugin: {e}")
        return False


def prompt_execute_python_consent() -> bool:
    """Interactive CLI prompt explaining benefits and security risks of execute_python."""
    print("\n" + "=" * 76)
    print(" [!] SECURITY GATING CONFIGURATION: `execute_code`")
    print("=" * 76)
    print(
        "The `execute_code` tool allows the AI assistant to run arbitrary Python or C# code\n"
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
    install_plugin: bool = True,
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
    if client_name:
        # An explicitly named client is configured even if it was not detected.
        targets = [c for c in all_clients if client_name.lower() in c.name.lower()]
        if not targets:
            logger.error(f"Unknown client '{client_name}'. Known: {', '.join(c.name for c in all_clients)}")
            return 1
    else:
        targets = [c for c in all_clients if c.is_installed()]
        if not targets:
            logger.error("No supported AI client was detected. Re-run with --client <name> to configure one anyway.")
            print("Locations checked:")
            for c in all_clients:
                print(f"  - {c.name}: {c.config_path}")
            return 1
    detected = targets

    print(f"\nClients to configure ({len(detected)}):")
    for c in detected:
        print(f"  [x] {c.name} (config: {c.config_path})")

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

    if install_plugin and any(c.mcp_dir for c in detected):
        if install_antigravity_plugin(python_exe=resolved_python, allow_exec=final_allow_exec, dry_run=dry_run):
            logger.info("Antigravity / Gemini SolidWorks plugin installed.")

    print("\n" + "=" * 76)
    print(f" [OK] Installation complete: {success_count}/{len(detected)} clients configured.")
    print(f" [*] Skill copies installed: {total_skills_installed}.")
    print("=" * 76)
    print("To use: Restart your AI client(s) (Claude Desktop, Antigravity, Cursor, etc.).\n")
    return 0 if success_count == len(detected) else 1


def main():
    parser = argparse.ArgumentParser(description="SolidWorks MCP Auto-Installer for AI Clients")
    parser.add_argument("--python", type=str, default=None, help="Path to Python executable to use in MCP config")
    parser.add_argument("--allow-exec", action="store_true", default=None, help="Permanently enable SW_MCP_ALLOW_EXEC=1 in configs")
    parser.add_argument("--no-allow-exec", action="store_false", dest="allow_exec", help="Enforce SW_MCP_ALLOW_EXEC=0 in configs")
    parser.add_argument("--client", type=str, default=None, help="Target a specific client by name")
    parser.add_argument("--no-install-plugin", action="store_false", dest="install_plugin",
                        help="Skip the Antigravity plugin bundle (only installed when Antigravity is a target)")
    parser.add_argument("--dry-run", action="store_true", help="Preview installation without modifying files")
    parser.add_argument("--non-interactive", action="store_true", help="Run without interactive prompts")

    args = parser.parse_args()
    ret = run_installer(
        python_exe=args.python,
        allow_exec=args.allow_exec,
        client_name=args.client,
        dry_run=args.dry_run,
        non_interactive=args.non_interactive,
        install_plugin=args.install_plugin,
    )
    sys.exit(ret)


if __name__ == "__main__":
    main()
