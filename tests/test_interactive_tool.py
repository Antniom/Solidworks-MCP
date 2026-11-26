"""show_interactive_studio, studio_action and the MCP Apps resource, exercised through the MCP SDK itself."""

from unittest.mock import MagicMock

import anyio
import pytest
from mcp.client import Client  # noqa: F401  (skips cleanly if the SDK layout changes)

from solidworks_mcp import server as srv
from solidworks_mcp.ui import studio


def test_tool_definition_exists_and_valid():
    tool = next(t for t in srv._tool_definitions() if t.name == "show_interactive_studio")
    assert tool.meta["ui"]["resourceUri"] == srv.STUDIO_RESOURCE_URI
    assert set(tool.input_schema["properties"]) == {"view_name", "feature_labels", "dimension_labels", "feature_filter"}
    action = next(t for t in srv._tool_definitions() if t.name == "studio_action")
    assert action.input_schema["required"] == ["action"]
    assert action.input_schema["properties"]["action"]["enum"] == list(srv.STUDIO_ACTIONS)


def test_resource_is_listed_and_readable_through_the_sdk():
    """Regression: the handlers used to take (params, ctx), so real clients got 'Resource not found'."""

    async def go():
        async with Client(srv.server) as client:
            listed = await client.list_resources()
            assert [str(r.uri) for r in listed.resources] == [srv.STUDIO_RESOURCE_URI]
            read = await client.read_resource(srv.STUDIO_RESOURCE_URI)
            content = read.contents[0]
            assert content.mime_type == "text/html;profile=mcp-app"
            assert "ui/initialize" in content.text and "studio_action" in content.text
            tools = await client.list_tools()
            by_name = {t.name: t for t in tools.tools}
            assert by_name["show_interactive_studio"].meta["ui"]["resourceUri"] == srv.STUDIO_RESOURCE_URI
            assert by_name["studio_action"].meta["ui"]["visibility"] == ["app"]

    anyio.run(go)


def test_view_is_self_contained():
    """MCP Apps views run under a strict CSP: nothing may be loaded from the network."""
    page = studio.render_page()
    for marker in ("<script src", "<link ", "http://", "https://cdn", "fetch(", "EventSource", "@import"):
        assert marker not in page, marker


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

    res = srv._show_interactive_studio_handler({"dimension_labels": {"D1@Boss-Extrude1@Bracket.Part": "Base Thickness"}})

    assert res["success"] is True
    data = res["data"]
    assert "studioUrl" not in data and "a2ui" not in data
    assert data["dimensions"][0]["label"] == "Base Thickness"
    state = data["studio"]
    assert state["title"] == "Bracket.SLDPRT" and state["view"] == "*Isometric"
    assert state["info"].startswith("1 x 2 x 3 mm")
    assert state["dims"][0]["key"] == "d0" and state["dims"][0]["value"] == 20.0
    assert "127.0.0.1" not in res["message"]


def test_studio_action_runs_through_the_mcp_server(monkeypatch):
    """The view calls the app-only studio_action tool via the host and gets the new state back."""
    automation = MagicMock()
    automation.set_dimension.return_value = {"success": True}
    automation.capture_viewport.return_value = {"success": False}
    automation.inspect_model.return_value = {"success": False}
    automation._result.side_effect = lambda ok, msg, code, data=None: {"success": ok, "message": msg}
    monkeypatch.setattr(srv, "sw_automation", automation)
    dims = studio.build_dimension_model(
        [{"full_name": "D1@Boss-Extrude1@P.Part", "name": "D1", "owner_feature": "Boss-Extrude1", "value": 20.0, "units": "mm"}]
    )
    studio.set_session("P.SLDPRT", dims)

    res = srv._dispatch_tool("studio_action", {"action": "set_dimension", "key": "d0", "value": 24.5})

    assert res["success"] is True
    automation.set_dimension.assert_called_once_with(name="D1@Boss-Extrude1@P.Part", value=24.5, units="mm")
    state = res["data"]["studio"]
    assert state["dims"][0]["value"] == 24.5
    assert "from 20 to 24.5 mm" in state["summary"]

    assert srv._dispatch_tool("studio_action", {"action": "explode"})["success"] is False
    assert srv._dispatch_tool("studio_action", {"action": "set_dimension", "key": "d0"})["success"] is False
