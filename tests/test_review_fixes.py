"""Regression tests for defects found in the plugin review."""

import json
import threading
import time
from unittest.mock import MagicMock

import pytest

from solidworks_mcp import server as srv
from solidworks_mcp.config import SolidWorksConfig
from solidworks_mcp.core.com_worker import STAWorker
from solidworks_mcp.tool_schemas import _NON_LENGTH_UNIT_TOOLS


# --- units ------------------------------------------------------------------

def _recording_automation(unit="mm"):
    automation = MagicMock()
    automation.units.default_unit = unit
    return automation


@pytest.mark.parametrize("tool,method", [
    ("extrude_sketch", "extrude_sketch"),      # used to follow set_units
    ("revolve_sketch", "revolve_sketch"),      # used to be hard-wired to mm
    ("create_shell", "create_shell"),
    ("add_component", "add_component"),
])
def test_every_length_tool_follows_the_session_unit(monkeypatch, tool, method):
    automation = _recording_automation("inch")
    monkeypatch.setattr(srv, "sw_automation", automation)
    srv._dispatch_tool(tool, {})
    assert getattr(automation, method).call_args.kwargs["unit"] == "inch"


def test_explicit_unit_wins_and_load_units_are_untouched(monkeypatch):
    automation = _recording_automation("inch")
    monkeypatch.setattr(srv, "sw_automation", automation)
    srv._dispatch_tool("create_shell", {"unit": "cm"})
    assert automation.create_shell.call_args.kwargs["unit"] == "cm"
    srv._dispatch_tool("add_study_load", {})
    kwargs = automation.add_study_load.call_args.kwargs
    assert kwargs["unit"] == "N/m^2" and kwargs["coord_unit"] == "inch"


def test_schemas_do_not_advertise_a_fixed_mm_default():
    for tool in srv._tool_definitions():
        unit = tool.input_schema["properties"].get("unit")
        if unit and tool.name not in _NON_LENGTH_UNIT_TOOLS:
            assert "default" not in unit, tool.name


# --- code execution gate ----------------------------------------------------

@pytest.mark.parametrize("language", ["python", "csharp", "csharp_macro"])
def test_every_language_shares_the_consent_gate(monkeypatch, language):
    monkeypatch.delenv("SW_MCP_ALLOW_EXEC", raising=False)
    monkeypatch.delenv("SWMCP_ENABLE_EXECUTE_PYTHON", raising=False)
    monkeypatch.setattr(srv, "_session_exec_unlocked", False)
    monkeypatch.setattr(srv.get_config(), "enable_execute_python", False)
    runner = MagicMock()
    monkeypatch.setattr(srv, "get_mecagent_runner", lambda: runner)

    res = srv._dispatch_tool("execute_code", {"language": language, "code": "1+1"})

    assert res["success"] is False and res["security_gate"] == "consent_required"
    assert not runner.method_calls, "locked code must never reach the runner"


# --- handler signatures ------------------------------------------------------

def test_handlers_use_the_sdk_argument_order():
    import inspect
    for handler in (srv.list_tools, srv.call_tool, srv.list_resources, srv.read_resource):
        assert list(inspect.signature(handler).parameters)[:2] == ["ctx", "params"], handler.__name__


# --- transactions -------------------------------------------------------------

def _transaction_fixture(monkeypatch, volumes, deleted):
    """volumes: successive capture_vol_state() results."""
    from solidworks_mcp import core
    from solidworks_mcp.automation.diagnostics import DiagnosticOperations

    it = iter(volumes)
    monkeypatch.setattr(core, "capture_vol_state", lambda doc: next(it))
    manager = MagicMock()
    manager.create_checkpoint.return_value = {"success": True}
    manager.rollback_checkpoint.return_value = {"success": True, "deleted_features": deleted}
    monkeypatch.setattr(core, "get_checkpoint_manager", lambda: manager)

    doc = MagicMock()
    self = MagicMock(spec=["get_active_doc", "_result", "_active_txn"])
    self._active_txn = None
    self.get_active_doc.return_value = (doc, None)
    self._result.side_effect = lambda ok, msg, code, data=None: {"success": ok, "message": msg, "data": data}
    return DiagnosticOperations.transaction, self, doc, manager


def test_transaction_begin_does_not_write_a_snapshot_and_names_are_unique(monkeypatch):
    txn, self, doc, manager = _transaction_fixture(monkeypatch, [1.0, 1.0, 1.0, 1.0, 1.0, 1.0], [])
    txn(self, "begin", "batch")
    first = manager.create_checkpoint.call_args.kwargs
    assert first["snapshot"] is False
    txn(self, "abort")
    txn(self, "begin", "batch")
    assert manager.create_checkpoint.call_args.kwargs["name"] != first["name"]


def test_abort_reports_failure_when_geometry_is_not_restored(monkeypatch):
    # begin=1.0; after feature rollback still 2.0; after undo still 2.0
    txn, self, doc, _ = _transaction_fixture(monkeypatch, [1.0, 2.0, 2.0], [])
    txn(self, "begin", "batch")
    res = txn(self, "abort")
    assert res["success"] is False and res["data"]["rolled_back"] is False
    assert "NOT" in res["message"]
    doc.EditUndo2.assert_called_once_with(1)


def test_abort_of_an_untouched_transaction_does_not_undo_earlier_work(monkeypatch):
    txn, self, doc, _ = _transaction_fixture(monkeypatch, [1.0, 1.0, 1.0], [])
    txn(self, "begin", "batch")
    res = txn(self, "abort")
    assert res["success"] is True
    doc.EditUndo2.assert_not_called()


# --- STA worker ---------------------------------------------------------------

def test_reset_does_not_leak_threads_and_old_thread_ignores_new_work():
    before = threading.active_count()
    worker = STAWorker(name="test-sta")
    worker.start()
    release = threading.Event()
    hung = worker.submit(release.wait, 5, tool_name="hung")
    time.sleep(0.3)

    worker.reset()
    assert worker.submit(lambda: threading.current_thread().name).result(timeout=2) == "test-sta"
    release.set()            # the abandoned call returns...
    hung.result(timeout=2)
    time.sleep(0.5)          # ...and its thread must exit instead of sharing the new queue
    assert threading.active_count() == before + 1
    assert not worker.is_abandoned_call_running
    worker.stop()


def test_abandoned_flag_is_not_left_behind_by_a_call_that_already_finished():
    worker = STAWorker(name="test-sta-2")
    worker.start()
    fut = worker.submit(lambda: 1, tool_name="quick")
    fut.result(timeout=2)
    worker._mark_abandoned(fut, "quick", time.time())
    assert not worker.is_abandoned_call_running
    worker.stop()


# --- config / viewport --------------------------------------------------------

def test_unknown_config_key_does_not_discard_the_rest(tmp_path):
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"default_unit": "inch", "startup_timeout": 7, "no_such_option": True}))
    cfg = SolidWorksConfig.load(str(cfg_file))
    assert cfg.default_unit == "inch" and cfg.startup_timeout == 7


@pytest.mark.parametrize("view,view_id", [
    ("*Front", 1), ("*Back", 2), ("*Left", 3), ("*Right", 4), ("*Top", 5), ("*Bottom", 6),
    ("*Isometric", 7), ("My Custom View", -1),
])
def test_capture_viewport_maps_every_standard_view(monkeypatch, tmp_path, view, view_id):
    from solidworks_mcp.automation import inspection
    from solidworks_mcp.automation.inspection import InspectionOperations

    policy = MagicMock()
    policy.validate_path.side_effect = lambda path, **k: (True, path, None)
    monkeypatch.setattr(inspection, "get_fs_policy", lambda: policy)
    doc = MagicMock()
    self = MagicMock()
    self.get_active_doc.return_value = (doc, None)
    InspectionOperations.capture_viewport(self, filepath=str(tmp_path / "v.png"), view_name=view)
    doc.ShowNamedView2.assert_called_once_with(view, view_id)
