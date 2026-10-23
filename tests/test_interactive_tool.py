"""
Unit tests for SolidWorks MCP show_interactive_studio Tool & MCP Resources
--------------------------------------------------------------------------
Tests tool registration, schema, _meta.ui.resourceUri, handler execution,
<agent-embed> formatting, and resource read with CSP connectDomains.
"""

import pytest
import asyncio
from unittest.mock import MagicMock, patch
from mcp.types import ReadResourceRequestParams
from solidworks_mcp.server import (
    _tool_definitions,
    list_resources,
    read_resource,
    _show_interactive_studio_handler,
)
from solidworks_mcp.ui.bridge import set_automation_instance, stop_bridge


@pytest.fixture(autouse=True)
def cleanup():
    stop_bridge()
    set_automation_instance(None)
    yield
    stop_bridge()
    set_automation_instance(None)


def test_tool_definition_exists_and_valid():
    """Verify show_interactive_studio is present in tool definitions with meta."""
    tools = _tool_definitions()
    studio_tool = next((t for t in tools if t.name == "show_interactive_studio"), None)
    assert studio_tool is not None, "show_interactive_studio tool missing from definitions"

    schema = getattr(studio_tool, "input_schema", getattr(studio_tool, "inputSchema", {}))
    props = schema.get("properties", {})
    assert "view_name" in props
    assert "feature_filter" in props

    meta = getattr(studio_tool, "meta", None)
    assert meta is not None, "Tool missing meta object"
    assert meta.get("ui", {}).get("resourceUri") == "ui://solidworks/app.html"


def test_list_resources():
    """Verify list_resources advertises ui://solidworks/app.html as mcp-app."""
    res = asyncio.run(list_resources())
    assert len(res.resources) >= 1
    studio_res = next((r for r in res.resources if str(r.uri) == "ui://solidworks/app.html"), None)
    assert studio_res is not None
    assert studio_res.mime_type == "text/html;profile=mcp-app"


def test_read_resource_csp_headers():
    """Verify reading ui://solidworks/app.html provides CSP connectDomains for Fast-Bridge."""
    params = ReadResourceRequestParams(uri="ui://solidworks/app.html")
    res = asyncio.run(read_resource(params))
    assert len(res.contents) == 1
    content = res.contents[0]
    assert content.mime_type == "text/html;profile=mcp-app"
    assert "<!DOCTYPE html>" in content.text or "<html" in content.text

    # Verify CSP allows local bridge
    csp = content.meta.get("ui", {}).get("csp", {})
    connect_domains = csp.get("connectDomains", [])
    assert any("127.0.0.1" in d for d in connect_domains)


def test_show_interactive_studio_handler(tmp_path):
    """Verify tool handler executes, generates HTML, and returns embed markup."""
    mock_automation = MagicMock()
    mock_doc = MagicMock()
    mock_doc.GetTitle.return_value = "TestPart.SLDPRT"
    mock_automation.get_active_doc.return_value = (mock_doc, None)
    mock_automation.get_dimensions.return_value = {
        "success": True,
        "data": [
            {"full_name": "D1@Sketch1", "name": "Width", "value": 100.0, "units": "mm", "owner_feature": "Sketch1"},
            {"full_name": "D1@Boss-Extrude1", "name": "Height", "value": 30.0, "units": "mm", "owner_feature": "Boss-Extrude1"}
        ]
    }
    mock_automation.capture_viewport.return_value = {"success": False}
    mock_automation.inspect_model.return_value = {
        "success": True,
        "data": {"dimensions_mm": {"dx": 100.0, "dy": 50.0, "dz": 30.0}}
    }
    set_automation_instance(mock_automation)

    with patch("solidworks_mcp.server.sw_automation", mock_automation):
        res = _show_interactive_studio_handler({"view_name": "*Isometric", "output_dir": str(tmp_path)})

    assert res.get("success") is True
    data = res.get("data", {})
    assert data.get("modelTitle") == "TestPart.SLDPRT"
    assert data.get("dimensionsCount") == 2
    assert "artifactUrl" in data
    assert "bridgeUrl" in data

    # Verify agent embed tag in message
    msg = res.get("message", "")
    assert "<agent-embed" in msg
    assert "TestPart.SLDPRT" in msg
    assert "D1@Sketch1" in msg
