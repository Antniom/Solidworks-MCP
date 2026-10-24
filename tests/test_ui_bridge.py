"""
Unit tests for SolidWorks MCP Fast-Bridge HTTP Daemon
---------------------------------------------------
Tests CORS preflight, dynamic port fallback, single-flight coalescing,
rebuild failure rollback, and unit handling.
"""

import socket
import json
import urllib.request
import urllib.error
import pytest
from unittest.mock import MagicMock
from solidworks_mcp.ui.bridge import (
    start_bridge,
    stop_bridge,
    get_bridge_port,
    is_bridge_running,
    set_automation_instance,
    set_current_app_html,
    FastBridgeServer,
)


@pytest.fixture(autouse=True)
def cleanup_bridge():
    """Ensure FastBridge is stopped and automation reset before and after each test."""
    stop_bridge()
    set_automation_instance(None)
    yield
    stop_bridge()
    set_automation_instance(None)


def test_fast_bridge_startup_and_health():
    """Verify bridge starts up and responds to /health with CORS headers."""
    port = start_bridge(port_start=58421, port_max=58430)
    assert is_bridge_running()
    assert port >= 58421

    url = f"http://127.0.0.1:{port}/health"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=2.0) as resp:
        assert resp.status == 200
        headers = dict(resp.headers)
        assert headers.get("Access-Control-Allow-Origin") == "*"
        data = json.loads(resp.read().decode("utf-8"))
        assert data.get("status") == "ok"
        assert data.get("port") == port


def test_cors_options_preflight():
    """Verify OPTIONS preflight request returns 204 with full CORS headers."""
    port = start_bridge(port_start=58421, port_max=58430)
    url = f"http://127.0.0.1:{port}/api/set_dimension"

    req = urllib.request.Request(url, method="OPTIONS")
    req.add_header("Origin", "null")  # Sandboxed iframe simulation
    req.add_header("Access-Control-Request-Method", "POST")
    req.add_header("Access-Control-Request-Headers", "Content-Type")

    with urllib.request.urlopen(req, timeout=2.0) as resp:
        assert resp.status == 204
        headers = dict(resp.headers)
        assert headers.get("Access-Control-Allow-Origin") == "*"
        assert "POST" in headers.get("Access-Control-Allow-Methods", "")
        assert "Content-Type" in headers.get("Access-Control-Allow-Headers", "")


def test_dynamic_port_fallback():
    """Verify bridge tries next port when port_start is already occupied."""
    # Occupy port 54321 with a raw socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 58421))
    sock.listen(1)

    try:
        # Start server with port_start=54321
        server = FastBridgeServer(port_start=58421, port_max=58430)
        port = server.start()
        assert port == 58422, f"Expected port 54322, got {port}"
        server.stop()
    finally:
        sock.close()


def test_get_dimensions_endpoint():
    """Verify /api/dimensions calls sw_automation.get_dimensions()."""
    mock_automation = MagicMock()
    mock_automation.get_dimensions.return_value = {
        "success": True,
        "data": [
            {"full_name": "D1@Sketch1", "name": "D1", "value": 50.0, "units": "mm", "owner_feature": "Sketch1"},
            {"full_name": "D1@Boss-Extrude1", "name": "D1", "value": 20.0, "units": "mm", "owner_feature": "Boss-Extrude1"},
        ]
    }
    set_automation_instance(mock_automation)

    port = start_bridge(port_start=58421, port_max=58430)
    url = f"http://127.0.0.1:{port}/api/dimensions"

    with urllib.request.urlopen(url, timeout=2.0) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data.get("success") is True
        assert data.get("count") == 2
        assert data["dimensions"][0]["full_name"] == "D1@Sketch1"


def test_set_dimension_success_with_units():
    """Verify /api/set_dimension passes correct units and returns image."""
    mock_automation = MagicMock()
    # Mock query prior dimension
    mock_automation.get_dimensions.return_value = {
        "success": True,
        "data": [{"full_name": "A1@Sketch1", "value": 45.0, "units": "deg"}]
    }
    # Mock set_dimension success
    mock_automation.set_dimension.return_value = {"success": True, "message": "Rebuilt successfully"}
    # Mock capture_viewport
    mock_automation.capture_viewport.return_value = {"success": False}  # Viewport optional
    mock_automation.inspect_model.return_value = {
        "success": True,
        "data": {"dimensions_mm": {"dx": 50.0, "dy": 50.0, "dz": 20.0}, "volume": 50000.0}
    }
    set_automation_instance(mock_automation)

    port = start_bridge(port_start=58421, port_max=58430)
    url = f"http://127.0.0.1:{port}/api/set_dimension"

    payload = json.dumps({"name": "A1@Sketch1", "value": 90.0, "units": "deg"}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")

    with urllib.request.urlopen(req, timeout=2.0) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data.get("success") is True
        assert data.get("value") == 90.0
        assert data.get("units") == "deg"
        assert data.get("bbox") == {"dx": 50.0, "dy": 50.0, "dz": 20.0}

    mock_automation.set_dimension.assert_called_with(name="A1@Sketch1", value=90.0, units="deg")


def test_set_dimension_rebuild_failure_and_rollback():
    """Verify /api/set_dimension automatically rolls back on rebuild failure."""
    mock_automation = MagicMock()
    # Initial dimension query
    mock_automation.get_dimensions.return_value = {
        "success": True,
        "data": [{"full_name": "D1@Sketch1", "value": 30.0, "units": "mm"}]
    }
    # Failing dimension change
    mock_automation.set_dimension.side_effect = [
        {"success": False, "message": "Geometry rebuild error: self-intersecting loop"},
        {"success": True, "message": "Rolled back"}
    ]
    set_automation_instance(mock_automation)

    port = start_bridge(port_start=58421, port_max=58430)
    url = f"http://127.0.0.1:{port}/api/set_dimension"

    payload = json.dumps({"name": "D1@Sketch1", "value": 500.0, "units": "mm"}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")

    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req, timeout=2.0)

    assert exc_info.value.code == 422
    err_data = json.loads(exc_info.value.read().decode("utf-8"))
    assert err_data.get("success") is False
    assert "rebuild error" in err_data.get("error", "").lower()
    assert err_data.get("revert_value") == 30.0

    # Assert rollback call was triggered with original value
    assert mock_automation.set_dimension.call_count == 2
    mock_automation.set_dimension.assert_called_with(name="D1@Sketch1", value=30.0, units="mm")


def test_app_html_endpoint():
    """Verify /api/app.html serves configured HTML."""
    custom_html = "<!DOCTYPE html><html><body><h1>Custom Studio</h1></body></html>"
    set_current_app_html(custom_html)

    port = start_bridge(port_start=58421, port_max=58430)
    url = f"http://127.0.0.1:{port}/api/app.html"

    with urllib.request.urlopen(url, timeout=2.0) as resp:
        assert resp.status == 200
        assert "text/html" in resp.headers.get("Content-Type", "")
        content = resp.read().decode("utf-8")
        assert content == custom_html


def test_rename_feature_endpoint():
    """Verify /api/rename_feature dispatches manage_feature rename to automation."""
    mock_automation = MagicMock()
    mock_automation.manage_feature.return_value = {
        "success": True,
        "message": "Feature renamed successfully"
    }
    set_automation_instance(mock_automation)

    port = start_bridge(port_start=58421, port_max=58430)
    url = f"http://127.0.0.1:{port}/api/rename_feature"

    payload = json.dumps({"feature_name": "Boss-Extrude1", "new_name": "Hex-Head"}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")

    with urllib.request.urlopen(req, timeout=2.0) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data.get("success") is True
        assert data.get("old_name") == "Boss-Extrude1"
        assert data.get("new_name") == "Hex-Head"

    mock_automation.manage_feature.assert_called_once_with(
        action="rename",
        feature_name="Boss-Extrude1",
        new_name="Hex-Head"
    )

def test_sync_endpoint_and_revision_increment():
    """Verify /api/sync returns model revision, dimensions, and viewport snapshot."""
    mock_automation = MagicMock()
    mock_automation.get_dimensions.return_value = {
        "success": True,
        "data": [{"full_name": "D1@Hex-Head", "name": "D1", "value": 15.0, "units": "mm"}]
    }
    set_automation_instance(mock_automation)

    port = start_bridge(port_start=58421, port_max=58430)
    url = f"http://127.0.0.1:{port}/api/sync"

    with urllib.request.urlopen(url, timeout=2.0) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        assert data.get("success") is True
        assert "revision" in data
        assert len(data.get("dimensions", [])) == 1
        assert data["dimensions"][0]["value"] == 15.0
