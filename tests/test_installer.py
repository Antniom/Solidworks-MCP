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


def test_register_mcp_server_updates_existing_extra_paths_only(tmp_path: Path):
    """Extra config files are mirrored when present and never created from nothing."""
    primary = tmp_path / "primary.json"
    extra = tmp_path / "extra.json"
    absent = tmp_path / "absent.json"
    extra.write_text('{"mcpServers": {"other": {"command": "x"}}}', encoding="utf-8")

    client = ClientTarget(name="TestClient", config_path=primary, extra_config_paths=[extra, absent])
    assert register_mcp_server(client, python_exe="python.exe", allow_exec=False) is True

    assert "solidworks" in json.loads(primary.read_text(encoding="utf-8"))["mcpServers"]
    extra_data = json.loads(extra.read_text(encoding="utf-8"))["mcpServers"]
    assert set(extra_data) == {"other", "solidworks"}
    assert not absent.exists()


def test_unparseable_config_is_never_overwritten(tmp_path: Path):
    """A broken client config (e.g. ~/.claude.json) must be left exactly as it is."""
    config_file = tmp_path / ".claude.json"
    original = '{"projects": {"a": 1}, "mcpServers": {  <-- truncated'
    config_file.write_text(original, encoding="utf-8")

    ok = _update_single_config(config_file, "solidworks", {"command": "python.exe"})

    assert ok is False
    assert config_file.read_text(encoding="utf-8") == original


def test_existing_config_is_backed_up_and_other_keys_survive(tmp_path: Path):
    config_file = tmp_path / ".claude.json"
    config_file.write_text('{"projects": {"a": 1}}', encoding="utf-8")

    assert _update_single_config(config_file, "solidworks", {"command": "python.exe"}) is True

    data = json.loads(config_file.read_text(encoding="utf-8"))
    assert data["projects"] == {"a": 1} and "solidworks" in data["mcpServers"]
    backup = tmp_path / ".claude.json.solidworks-mcp.bak"
    assert json.loads(backup.read_text(encoding="utf-8")) == {"projects": {"a": 1}}


def test_vscode_uses_servers_key(tmp_path: Path):
    client = ClientTarget(name="VS Code", config_path=tmp_path / "mcp.json", config_format="json_servers")
    assert register_mcp_server(client, python_exe="python.exe") is True
    data = json.loads((tmp_path / "mcp.json").read_text(encoding="utf-8"))
    assert "mcpServers" not in data
    assert data["servers"]["solidworks"]["type"] == "stdio"


def test_codex_toml_block_is_replaced_not_duplicated(tmp_path: Path):
    import tomllib

    config_file = tmp_path / "config.toml"
    config_file.write_text(
        'model = "gpt"\n\n[mcp_servers.other]\ncommand = "o"\n\n'
        '[mcp_servers.solidworks]\ncommand = "old.exe"\n\n[mcp_servers.solidworks.env]\nOLD = "1"\n\n'
        '[profiles.x]\nk = 1\n',
        encoding="utf-8",
    )
    client = ClientTarget(name="Codex CLI", config_path=config_file, config_format="toml_mcp_servers")
    for _ in range(2):  # idempotent
        assert register_mcp_server(client, python_exe=r"C:\\Py\\python.exe", allow_exec=True) is True

    data = tomllib.loads(config_file.read_text(encoding="utf-8"))
    assert data["model"] == "gpt" and data["profiles"]["x"]["k"] == 1
    assert data["mcp_servers"]["other"]["command"] == "o"
    sw = data["mcp_servers"]["solidworks"]
    assert sw["command"] == r"C:\\Py\\python.exe" and sw["args"] == ["-m", "solidworks_mcp.server"]
    assert sw["env"]["SW_MCP_ALLOW_EXEC"] == "1" and "OLD" not in sw["env"]


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

