"""Studio bridge: authentication, A2UI action handling and cleanup."""

import glob
import json
import os
import tempfile
import urllib.error
import urllib.request

import pytest

from solidworks_mcp.ui import a2ui, bridge


class FakeAutomation:
    """Stands in for SolidWorksAutomation (no run_on_sta, so calls are direct)."""

    def __init__(self):
        self.set_calls = []
        self.fail_above = None

    def capture_viewport(self, filepath=None, view_name="*Isometric"):
        with open(filepath, "wb") as f:
            f.write(b"\x89PNG fake " + view_name.encode())
        return {"success": True, "data": {"filepath": filepath}}

    def inspect_model(self):
        return {"success": True, "data": {"dimensions_mm": {"dx": 10.0, "dy": 20.0, "dz": 30.0}}}

    def set_dimension(self, name, value, units=None):
        self.set_calls.append((name, value, units))
        if self.fail_above is not None and value > self.fail_above:
            return {"success": False, "message": "Rebuild failed"}
        return {"success": True, "message": "ok"}


@pytest.fixture()
def studio():
    automation = FakeAutomation()
    bridge.set_automation_instance(automation)
    port = bridge.start_bridge(port_start=54400, port_max=54420)
    dims = a2ui.build_dimension_model(
        [{"full_name": "D1@Boss-Extrude1@P.Part", "name": "D1", "owner_feature": "Boss-Extrude1", "value": 20.0, "units": "mm"}]
    )
    bridge.set_session("P.SLDPRT", dims, image="", bbox=None)
    yield automation, f"http://127.0.0.1:{port}"
    bridge.stop_bridge()
    bridge.set_automation_instance(None)


def _request(url, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _action(name, context):
    return {"version": "v0.9", "action": {
        "name": name, "surfaceId": a2ui.SURFACE_ID, "sourceComponentId": "slider-d0",
        "timestamp": "2026-01-01T00:00:00Z", "context": context}}


def test_health_is_open_but_api_needs_the_token(studio):
    automation, base = studio
    assert _request(f"{base}/health")[0] == 200
    assert _request(f"{base}/api/a2ui")[0] == 403
    assert _request(f"{base}/api/a2ui?token=wrong")[0] == 403
    assert _request(f"{base}/studio")[0] == 403
    status, _ = _request(f"{base}/api/a2ui/action", _action("set_dimension", {"key": "d0", "value": 25}))
    assert status == 403
    assert automation.set_calls == [], "an unauthenticated request must never reach SolidWorks"


def test_foreign_host_header_is_rejected(studio):
    # DNS-rebinding: the right token but a non-loopback Host must still be refused.
    _, base = studio
    status, _ = _request(f"{base}/api/a2ui?token={bridge.get_token()}", headers={"Host": "evil.example"})
    assert status == 403


def test_page_and_messages_with_token(studio):
    _, base = studio
    token = bridge.get_token()
    status, page = _request(f"{base}/studio?token={token}")
    assert status == 200 and b"a2ui-surface" in page and token.encode() in page
    status, body = _request(f"{base}/api/a2ui?token={token}")
    messages = json.loads(body)["messages"]
    assert status == 200 and "createSurface" in messages[0]


def test_set_dimension_action_drives_solidworks(studio):
    automation, base = studio
    status, body = _request(
        f"{base}/api/a2ui/action?token={bridge.get_token()}", _action("set_dimension", {"key": "d0", "value": 25})
    )
    assert status == 200
    assert automation.set_calls == [("D1@Boss-Extrude1@P.Part", 25.0, "mm")]
    updates = {m["updateDataModel"]["path"]: m["updateDataModel"]["value"] for m in json.loads(body)["messages"]}
    assert updates["/dims/d0/value"] == 25.0
    assert updates["/viewport/image"].startswith("data:image/png;base64,")
    assert "from 20 to 25 mm" in updates["/summary"]


def test_failed_rebuild_reverts_the_slider(studio):
    automation, base = studio
    automation.fail_above = 22
    _, body = _request(
        f"{base}/api/a2ui/action?token={bridge.get_token()}", _action("set_dimension", {"key": "d0", "value": 25})
    )
    updates = {m["updateDataModel"]["path"]: m["updateDataModel"]["value"] for m in json.loads(body)["messages"]}
    assert updates["/dims/d0/value"] == 20.0
    assert "Rebuild failed" in updates["/status"]
    assert bridge.get_session().dims["d0"]["value"] == 20.0


def test_values_are_clamped_and_unknown_keys_ignored(studio):
    automation, base = studio
    url = f"{base}/api/a2ui/action?token={bridge.get_token()}"
    _request(url, _action("set_dimension", {"key": "d0", "value": 1e9}))
    assert automation.set_calls[-1][1] == bridge.get_session().dims["d0"]["max"]
    before = len(automation.set_calls)
    # The dimension name is looked up server-side from the key; a client cannot name arbitrary dimensions.
    _request(url, _action("set_dimension", {"key": "nope", "name": "D1@Other", "value": 5}))
    assert len(automation.set_calls) == before


def test_malformed_action_is_rejected(studio):
    _, base = studio
    status, _ = _request(f"{base}/api/a2ui/action?token={bridge.get_token()}", {"not": "an action"})
    assert status == 400


def test_viewport_captures_do_not_accumulate(studio):
    pattern = os.path.join(tempfile.gettempdir(), "swmcp_studio_*.png")
    before = set(glob.glob(pattern))
    for _ in range(3):
        assert bridge.capture_viewport_data_uri().startswith("data:image/png;base64,")
    assert set(glob.glob(pattern)) == before


def test_start_fails_loudly_when_no_port_is_free():
    blocker = bridge.StudioBridgeServer(port_start=54450, port_max=54450)
    blocker.start()
    try:
        with pytest.raises(RuntimeError):
            bridge.StudioBridgeServer(port_start=54450, port_max=54450).start()
    finally:
        blocker.stop()
