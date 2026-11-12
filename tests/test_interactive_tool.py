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
            assert bridge.get_token() not in content.text
            tools = await client.list_tools()
            by_name = {t.name: t for t in tools.tools}
            assert "show_interactive_studio" in by_name
            assert by_name["studio_action"].meta["ui"]["visibility"] == ["app"]

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
    assert set(data["a2ui"]) == {"messages"}, "no bridge address or token in the tool result"
    assert data["a2ui"]["messages"][0]["version"] == "1.0" and "createSurface" in data["a2ui"]["messages"][0]
    assert "<agent-embed" not in res["message"]


def test_studio_still_opens_inline_when_the_bridge_cannot_start(monkeypatch):
    automation = MagicMock()
    doc = MagicMock()
    doc.GetTitle = "Bracket.SLDPRT"
    automation.get_active_doc.return_value = (doc, None)
    automation.get_dimensions.return_value = {"success": True, "data": []}
    automation.capture_viewport.return_value = {"success": False}
    automation.inspect_model.return_value = {"success": False}
    monkeypatch.setattr(srv, "sw_automation", automation)

    def boom(*a, **k):
        raise RuntimeError("no free port")

    monkeypatch.setattr(bridge, "start_bridge", boom)
    res = srv._show_interactive_studio_handler({})
    assert res["success"] is True
    assert res["data"]["studioUrl"] is None and "54321" not in res["message"]
    assert "createSurface" in res["data"]["a2ui"]["messages"][0]


def test_studio_action_runs_through_the_mcp_server(monkeypatch):
    """The in-app transport: the view calls the studio_action tool via the host."""
    from solidworks_mcp.ui import a2ui

    automation = MagicMock()
    automation.set_dimension.return_value = {"success": True}
    automation.capture_viewport.return_value = {"success": False}
    automation.inspect_model.return_value = {"success": False}
    automation._result.side_effect = lambda ok, msg, code, data=None: {"success": ok, "message": msg}
    monkeypatch.setattr(srv, "sw_automation", automation)
    dims = a2ui.build_dimension_model(
        [{"full_name": "D1@Boss-Extrude1@P.Part", "name": "D1", "owner_feature": "Boss-Extrude1", "value": 20.0, "units": "mm"}]
    )
    bridge.set_session("P.SLDPRT", dims)

    message = {"version": "1.0", "action": {"name": "set_dimension", "surfaceId": a2ui.SURFACE_ID,
                                            "sourceComponentId": "slider_d0", "timestamp": "2026-01-01T00:00:00Z",
                                            "context": {"key": "d0", "value": 24.5}}}
    res = srv._dispatch_tool("studio_action", {"message": message})

    assert res["success"] is True
    automation.set_dimension.assert_called_once_with(name="D1@Boss-Extrude1@P.Part", value=24.5, units="mm")
    updates = {m["updateDataModel"]["path"]: m["updateDataModel"]["value"] for m in res["data"]["a2ui"]["messages"]}
    assert updates["/dims/d0/value"] == 24.5

    bad = srv._dispatch_tool("studio_action", {"message": {"version": "1.0"}})
    assert bad["success"] is False
