"""
Unit tests for SolidWorks MCP auto-installer (solidworks_mcp.install).
"""

import json
from pathlib import Path
from solidworks_mcp.install import (
    ClientTarget,
    get_detected_clients,
    _update_single_config,
    register_mcp_server,
)


def test_antigravity_target_configuration():
    """Verify Antigravity target points to ~/.gemini/config/mcp_config.json."""
    clients = get_detected_clients()
    antigravity = next((c for c in clients if "Antigravity" in c.name), None)
    assert antigravity is not None, "Antigravity / Gemini client must be defined"
    
    # Must target global config path
    assert antigravity.config_path.parts[-3:] == (".gemini", "config", "mcp_config.json")
    
    # Must also mirror to legacy antigravity subfolder
    assert any(p.parts[-3:] == (".gemini", "antigravity", "mcp_config.json") for p in antigravity.extra_config_paths)


def test_update_single_config_strips_utf8_bom(tmp_path: Path):
    """Verify JSON files with UTF-8 BOM are loaded and rewritten without BOM."""
    config_file = tmp_path / "mcp_config.json"
    
    # Write initial file with UTF-8 BOM (\xef\xbb\xbf)
    initial_content = '\ufeff{\n  "mcpServers": {\n    "existing_tool": {\n      "command": "tool.exe"\n    }\n  }\n}'
    with open(config_file, "w", encoding="utf-8") as f:
        f.write(initial_content)
        
    entry = {"command": "python.exe", "args": ["-m", "solidworks_mcp.server"]}
    ok = _update_single_config(config_file, "solidworks", entry)
    assert ok is True
    
    # Verify file starts with '{' (no BOM bytes)
    with open(config_file, "rb") as f:
        raw_bytes = f.read(5)
        assert raw_bytes[0] == ord("{")
        
    # Verify both servers are present
    with open(config_file, "r", encoding="utf-8") as f:
        data = json.load(f)
        assert "existing_tool" in data["mcpServers"]
        assert "solidworks" in data["mcpServers"]
        assert data["mcpServers"]["solidworks"]["command"] == "python.exe"


def test_register_mcp_server_updates_extra_paths(tmp_path: Path):
    """Verify primary and extra config paths are updated in tandem."""
    primary = tmp_path / "primary.json"
    extra = tmp_path / "extra.json"
    
    client = ClientTarget(
        name="TestClient",
        config_path=primary,
        extra_config_paths=[extra],
    )
    
    ok = register_mcp_server(client, python_exe="python.exe", allow_exec=False)
    assert ok is True
    
    assert primary.exists()
    assert extra.exists()
    
    for path in (primary, extra):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            assert "solidworks" in data["mcpServers"]


def test_text_and_extrude_parameters():
    """Verify new reverse_dir, angle, and center properties exist in server tool schemas."""
    from solidworks_mcp.server import _tool_definitions
    tools = {t.name: t for t in _tool_definitions()}
    
    assert "extrude_sketch" in tools
    extrude_props = tools["extrude_sketch"].input_schema["properties"]
    assert "reverse_dir" in extrude_props
    assert extrude_props["reverse_dir"]["type"] == "boolean"
    
    assert "draw_text" in tools
    text_props = tools["draw_text"].input_schema["properties"]
    assert "angle" in text_props
    assert "center" in text_props

