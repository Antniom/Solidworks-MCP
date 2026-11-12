"""show_interactive_studio and the MCP Apps resource, exercised through the MCP SDK itself."""

from unittest.mock import MagicMock

import anyio
import pytest
from mcp.client import Client  # noqa: F401  (skips cleanly if the SDK layout changes)

from solidworks_mcp import server as srv
from solidworks_mcp.ui import bridge


def test_tool_definition_exists_and_valid():
    tool = next(t for t in srv._tool_definitions() if t.name == "show_interactive_studio")
    assert tool.meta["ui"]["resourceUri"] == srv.STUDIO_RESOURCE_URI
    assert set(tool.input_schema["properties"]) == {"view_name", "feature_labels", "dimension_labels", "feature_filter"}


def test_resource_is_listed_and_readable_through_the_sdk():
    """Regression: the handlers used to take (params, ctx), so real clients got 'Resource not found'."""

    async def go():
        async with Client(srv.server) as client:
            listed = await client.list_resources()
            assert [str(r.uri) for r in listed.resources] == [srv.STUDIO_RESOURCE_URI]
            read = await client.read_resource(srv.STUDIO_RESOURCE_URI)
            content = read.contents[0]
            assert content.mime_type == srv.STUDIO_MIME_TYPE
            assert "<div id=\"studio\">" in content.text
            assert bridge.get_token() in content.text
            tools = await client.list_tools()
            assert any(t.name == "show_interactive_studio" for t in tools.tools)

    anyio.run(go)


def test_unknown_resource_is_an_error():
    params = MagicMock()
    params.uri = "ui://solidworks/other.html"
    with pytest.raises(ValueError):
        anyio.run(srv.read_resource, None, params)


def test_show_interactive_studio_handler(monkeypatch):
    automation = MagicMock()
    doc = MagicMock()
    doc.GetTitle = "Bracket.SLDPRT"
    automation.get_active_doc.return_value = (doc, None)
    automation.get_dimensions.return_value = {"success": True, "data": [
        {"full_name": "D1@Boss-Extrude1@Bracket.Part", "name": "D1", "owner_feature": "Boss-Extrude1", "value": 20.0, "units": "mm"},
    ]}
    automation.capture_viewport.return_value = {"success": False}
    automation.inspect_model.return_value = {"success": True, "data": {"dimensions_mm": {"dx": 1.0, "dy": 2.0, "dz": 3.0}}}
    monkeypatch.setattr(srv, "sw_automation", automation)
    monkeypatch.setattr(bridge, "start_bridge", lambda *a, **k: 54321)
    monkeypatch.setattr(bridge, "_bridge_port", 54321)

    res = srv._show_interactive_studio_handler({"dimension_labels": {"D1@Boss-Extrude1@Bracket.Part": "Base Thickness"}})

    assert res["success"] is True
    data = res["data"]
    assert data["studioUrl"].startswith("http://127.0.0.1:54321/studio?token=")
    assert data["dimensions"][0]["label"] == "Base Thickness"
    kinds = [next(k for k in m if k != "version") for m in data["a2ui"]["messages"]]
    assert kinds == ["createSurface", "updateComponents", "updateDataModel"]
    assert "<agent-embed" not in res["message"]


def test_studio_reports_bridge_failure_instead_of_guessing_a_port(monkeypatch):
    automation = MagicMock()
    automation.get_active_doc.return_value = (MagicMock(), None)
    automation._result.side_effect = lambda ok, msg, code, data=None: {"success": ok, "message": msg}
    monkeypatch.setattr(srv, "sw_automation", automation)

    def boom(*a, **k):
        raise RuntimeError("no free port")

    monkeypatch.setattr(bridge, "start_bridge", boom)
    res = srv._show_interactive_studio_handler({})
    assert res["success"] is False and "no free port" in res["message"]
