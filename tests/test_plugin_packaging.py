"""
Unit tests for SolidWorks Antigravity Plugin Packaging
------------------------------------------------------
Validates plugin.json manifest schema, vector SVG logo well-formedness,
rules/AGENTS.md, mcp_config.json, and install.py plugin staging into tmp_path.
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
    assert data.get("name") == "solidworks"
    assert data.get("displayName") == "SolidWorks AI Studio"
    assert "description" in data
    assert len(data.get("description", "")) > 10
    assert data.get("version") == "4.1.0"
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


def test_plugin_rules_and_config():
    """Verify rules/AGENTS.md and mcp_config.json exist in plugin bundle."""
    rules_path = Path("plugins/solidworks/rules/AGENTS.md")
    assert rules_path.exists()
    assert "show_interactive_studio" in rules_path.read_text(encoding="utf-8")

    mcp_config_path = Path("plugins/solidworks/mcp_config.json")
    assert mcp_config_path.exists()
    data = json.loads(mcp_config_path.read_text(encoding="utf-8"))
    assert "mcpServers" in data
    assert "solidworks" in data["mcpServers"]


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
    assert len(list(skills_dir.glob("*/SKILL.md"))) == 11
