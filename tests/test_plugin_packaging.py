"""
Unit tests for SolidWorks Antigravity Plugin Packaging
------------------------------------------------------
Validates plugin.json manifest schema, vector SVG logo well-formedness,
the single skills/rules source, version agreement, and install.py plugin staging into tmp_path.
"""

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from solidworks_mcp.install import install_antigravity_plugin


def test_plugin_json_schema():
    """Verify plugin.json manifest has required keys and valid types."""
    manifest_path = Path("plugins/solidworks/plugin.json")
    assert manifest_path.exists(), "plugins/solidworks/plugin.json does not exist"

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data.get("name") == "solidcadworks"
    assert data.get("displayName") == "SolidCADWorks"
    assert "description" in data
    assert len(data.get("description", "")) > 10
    from solidworks_mcp import __version__
    assert data.get("version") == __version__
    assert data.get("logo") == "assets/logo.svg"

    prompts = data.get("suggestedPrompts", [])
    assert isinstance(prompts, list)
    assert len(prompts) == 3


def test_logo_svg_validity():
    """Verify assets/logo.svg exists and parses as valid XML/SVG with viewBox."""
    logo_path = Path("plugins/solidworks/assets/logo.svg")
    assert logo_path.exists(), "plugins/solidworks/assets/logo.svg missing"

    tree = ET.parse(str(logo_path))
    root = tree.getroot()
    assert "svg" in root.tag.lower()
    assert root.attrib.get("viewBox") == "0 0 128 128"


def test_single_source_of_skills_and_rules():
    """Skills and rules live once at the repo root; every manifest points at them."""
    assert Path("rules/AGENTS.md").exists()
    assert "show_interactive_studio" in Path("rules/AGENTS.md").read_text(encoding="utf-8")
    skills = sorted(p.parent.name for p in Path("skills").glob("*/SKILL.md"))
    assert "solidworks-modeling" in skills and not any(s.startswith("legacy-") for s in skills)
    assert not Path("solidworks_mcp/skills").exists() and not Path("plugins/solidworks/skills").exists()

    for manifest in (".codex-plugin/plugin.json", ".cursor-plugin/plugin.json"):
        data = json.loads(Path(manifest).read_text(encoding="utf-8"))
        assert (Path(data["skills"]) / "solidworks-modeling" / "SKILL.md").exists(), manifest
        assert Path(data["mcpServers"]).exists(), manifest


# --- Claude plugin rules (claude.com/docs/plugins/build and platform-support) ----------------------

def _plugin_files():
    import subprocess
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True).stdout
    return [Path(p) for p in out.splitlines() if p]


def test_claude_manifest_meets_directory_rules():
    import re
    data = json.loads(Path(".claude-plugin/plugin.json").read_text(encoding="utf-8"))
    assert re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?", data["name"])
    assert data["name"] == "solidcadworks" and data["displayName"] == "SolidCADWorks"
    marketplace = json.loads(Path(".claude-plugin/marketplace.json").read_text(encoding="utf-8"))
    assert [p["name"] for p in marketplace["plugins"]] == [data["name"]]
    for field in ("displayName", "version", "description", "author", "license"):
        assert data.get(field), field
    # Components are found by convention; only the manifest lives in .claude-plugin/.
    assert "skills" not in data and "mcpServers" not in data
    assert sorted(p.name for p in Path(".claude-plugin").iterdir()) == ["marketplace.json", "plugin.json"]
    assert Path("LICENSE").exists()

    readme = re.sub(r"```.*?```", "", Path("README.md").read_text(encoding="utf-8"), flags=re.S)
    assert len(readme.split()) >= 40


def test_local_connector_command_is_reviewable():
    servers = json.loads(Path(".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    sw = servers["solidworks"]
    # Launched through the bundled script, so it works without uv: cmd.exe always exists on Windows.
    assert sw["command"] == "cmd" and sw["args"] == ["/d", "/c", "${CLAUDE_PLUGIN_ROOT}/scripts/start-server.cmd"]
    joined = " ".join(sw["args"])
    assert ".." not in joined and "$" not in joined.replace("${CLAUDE_PLUGIN_ROOT}", "")
    assert not any(k.upper().endswith(("KEY", "TOKEN", "SECRET")) for k in sw.get("env", {}))
    # The script still prefers a pinned uv run (--locked plus the committed lock file) when uv exists.
    script = Path("scripts/start-server.cmd").read_bytes()
    assert b"\r\n" in script and b"\n" not in script.replace(b"\r\n", b""), "start-server.cmd needs CRLF"
    assert b"uv run --quiet --locked" in script and Path("uv.lock").exists()
    assert Path("scripts/bootstrap_env.py").exists() and Path("scripts/pyprobe.py").exists()


def test_bootstrap_reads_dependencies_from_pyproject():
    import importlib.util
    import tomllib
    spec = importlib.util.spec_from_file_location("bootstrap_env", "scripts/bootstrap_env.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    expected = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    assert mod.read_dependencies(Path(".")) == expected


def test_server_sends_an_icon():
    from solidworks_mcp import server
    assert Path("solidworks_mcp/assets/icon-128.png").exists()
    icons = server._server_icons()
    assert icons and icons[0].src.startswith("data:image/png;base64,") and icons[0].sizes == ["128x128"]


def test_plugin_folder_stays_within_marketplace_limits():
    """
    Limits for a plugin added by marketplace URL or upload (5,000 files, 200 MB).
    The bundled .NET runner rules out Anthropic's public directory, which caps files at 5 MiB.
    """
    files = _plugin_files()
    assert len(files) <= 5000
    assert sum(f.stat().st_size for f in files) < 200 * 1024 * 1024
    assert not Path("bin").exists(), "a top-level bin/ stops claude.ai and Cowork from installing the plugin"
    banned = {".ds_store", "thumbs.db", "desktop.ini"}
    assert not [f for f in files if f.name.lower() in banned]
    # Generated compile caches and debug symbols are never committed.
    assert not [str(f) for f in files if ".bundle_cache" in f.parts or f.suffix.lower() == ".pdb"]


def test_skill_frontmatter_is_valid():
    for skill in Path("skills").glob("*/SKILL.md"):
        text = skill.read_text(encoding="utf-8")
        assert text.startswith("---\n"), skill
        front = text.split("---\n")[1]
        assert f"name: {skill.parent.name}\n" in front, skill
        assert "description:" in front, skill


def test_versions_agree():
    from solidworks_mcp import __version__
    for manifest in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json", ".cursor-plugin/plugin.json",
                     "plugins/solidworks/plugin.json"):
        assert json.loads(Path(manifest).read_text(encoding="utf-8"))["version"] == __version__, manifest


def test_console_script_targets_a_sync_function():
    import inspect
    import tomllib
    from solidworks_mcp import server
    target = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]["solidworks-mcp"]
    func = getattr(server, target.split(":")[1])
    assert not inspect.iscoroutinefunction(func)


def test_plugin_staging_in_temp_dir(tmp_path: Path):
    """Test install_antigravity_plugin staging into an isolated directory."""
    target_plugin_dir = tmp_path / "test_plugin_solidworks"
    python_exe = "C:\\test\\python.exe"

    ok = install_antigravity_plugin(
        python_exe=python_exe,
        target_dir=target_plugin_dir,
        allow_exec=True,
        dry_run=False,
    )
    assert ok is True
    assert target_plugin_dir.exists()

    # Verify staged files
    assert (target_plugin_dir / "plugin.json").exists()
    assert (target_plugin_dir / "assets" / "logo.svg").exists()
    assert (target_plugin_dir / "rules" / "AGENTS.md").exists()
    assert (target_plugin_dir / "mcp_config.json").exists()

    # Check generated mcp_config.json content
    mcp_cfg = json.loads((target_plugin_dir / "mcp_config.json").read_text(encoding="utf-8"))
    sw_server = mcp_cfg["mcpServers"]["solidworks"]
    assert sw_server["command"] == python_exe
    assert sw_server["env"].get("SW_MCP_ALLOW_EXEC") == "1"

    # Verify skills copied
    skills_dir = target_plugin_dir / "skills"
    assert skills_dir.exists()
    assert (skills_dir / "solidworks-interactive" / "SKILL.md").exists()
    assert (skills_dir / "solidworks-modeling" / "SKILL.md").exists()
    assert (skills_dir / "solidworks-simulation" / "SKILL.md").exists()
    assert (skills_dir / "solidworks-dfm" / "SKILL.md").exists()
    assert (skills_dir / "solidworks-drawings" / "SKILL.md").exists()
    assert len(list(skills_dir.glob("*/SKILL.md"))) == len(list(Path("skills").glob("*/SKILL.md")))

    # Staging must not write back into the source tree
    assert not Path("plugins/solidworks/mcp_config.json").exists()


def test_installer_leaves_claude_code_to_the_plugin(tmp_path, monkeypatch):
    """Claude Code gets the server and skills from the SolidCADWorks plugin. An installer entry as well would
    register a second `solidworks` server in ~/.claude.json and a second copy of every skill."""
    from solidworks_mcp import install as ins
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    clients = ins.get_detected_clients()
    assert not [c for c in clients if "claude code" in c.name.lower()]
    assert not [c for c in clients if c.config_path.name == ".claude.json"]
    assert not [d for c in clients for d in c.skills_dirs if ".claude" in d.parts]
