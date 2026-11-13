"""
Each merged tool must reach the same automation call the separate tool used to make.
The underlying SolidWorks methods were not changed by the merge, only the routing.
"""

from unittest.mock import MagicMock

import pytest

from solidworks_mcp import server as srv


@pytest.fixture()
def automation(monkeypatch):
    mock = MagicMock()
    mock.units.default_unit = "mm"
    mock._result.side_effect = lambda ok, msg, code=None, data=None: {"success": ok, "message": msg}
    monkeypatch.setattr(srv, "sw_automation", mock)
    return mock


@pytest.fixture()
def runner(monkeypatch):
    mock = MagicMock()
    mock.invoke_sdk_method.return_value = {"success": True, "details": "ok"}
    mock.execute_csharp.return_value = {"success": True}
    mock.execute_macro.return_value = {"success": True}
    mock.capture_views.return_value = {"success": True}
    monkeypatch.setattr(srv, "get_mecagent_runner", lambda: mock)
    monkeypatch.setenv("SW_MCP_ALLOW_EXEC", "1")
    return mock


def call(tool, /, **arguments):
    assert srv._validate_tool_arguments(tool, arguments) is None, srv._validate_tool_arguments(tool, arguments)
    return srv._dispatch_tool(tool, arguments)


def test_create_document(automation):
    call("create_document")
    automation.create_new_part.assert_called_once_with()
    call("create_document", type="assembly")
    automation.create_new_assembly.assert_called_once_with()
    call("create_document", type="drawing", paper_size="A3")
    automation.create_new_drawing.assert_called_once_with(paper_size="A3")


def test_create_sketch_on_plane_or_face_point(automation):
    call("create_sketch", plane="Top", offset=5.0)
    automation.create_sketch.assert_called_once_with(plane="Top", offset=5.0, face_index=None, unit="mm")
    call("create_sketch", face_point=[1, 2, 3], unit="inch")
    automation.create_sketch_on_face.assert_called_once_with(x=1, y=2, z=3, unit="inch")


def test_select_by_name_and_point(automation):
    call("select", by="name", target_type="plane", name="Front Plane")
    automation.select_entity.assert_called_with(
        target_type="plane", name="Front Plane", point=None, unit="mm", direction=None, mark=0, append=False)
    call("select", by="point", point=[0, 0, 10], append=True)
    automation.select_entity.assert_called_with(
        target_type="face", name=None, point=[0, 0, 10], unit="mm", direction=None, mark=0, append=True)


def test_select_by_geometry(automation):
    call("select", by="geometry", target_type="face", surface_type="cylinder", radius=5.0)
    kwargs = automation.select_by_geometry.call_args.kwargs
    assert kwargs["target_type"] == "face" and kwargs["surface_type"] == "cylinder" and kwargs["radius"] == 5.0
    assert kwargs["normal_tolerance"] == 0.05 and kwargs["unit"] == "mm" and kwargs["append"] is False


def test_select_by_centroid(automation):
    call("select", by="centroid", x=0, y=0, z=20)
    automation.find_face.assert_called_once_with(
        min_area=10.0, surface_type="plane", body_index=1, x=0, y=0, z=20, tolerance=0.5, select=True)
    call("select", by="centroid", target_type="edge", x=5, locate_only=True)
    automation.find_edge.assert_called_once_with(body_index=1, x=5, y=None, z=None, tolerance=0.5, select=False)


def test_select_by_path(automation, runner):
    call("select", by="path", path="Boss-Extrude1/Face[0]", append=True, mark=4)
    runner.invoke_sdk_method.assert_called_once_with(
        "selectByPath", {"path": "Boss-Extrude1/Face[0]", "multiSelect": True, "mark": 4})


def test_select_requires_a_method(automation):
    assert srv._validate_tool_arguments("select", {"name": "Front Plane"}) is not None


def test_list_topology(automation):
    call("list_topology", kind="faces", min_size=25.0)
    automation.list_faces.assert_called_once_with(body_index=1, min_area=25.0, max_count=60)
    call("list_topology", kind="edges", max_count=5)
    automation.list_edges.assert_called_once_with(body_index=1, min_length=0.0, max_count=5)


def test_get_feature_tree(automation, runner, monkeypatch):
    monkeypatch.setattr(srv, "_list_features_fixed", lambda: {"success": True, "message": "listed"})
    assert call("get_feature_tree")["message"] == "listed"
    call("get_feature_tree", detail="parametric")
    runner.invoke_sdk_method.assert_called_with("getFeatureTreeJson", {})
    call("get_feature_tree", detail="context", depth=3, target_path="Boss-Extrude1")
    runner.invoke_sdk_method.assert_called_with(
        "getContextFast", {"depth": 3, "targetPath": "Boss-Extrude1", "documentName": None})


def test_inspect_topology(automation, runner):
    call("inspect_topology", path="Boss-Extrude1", show_equations=True)
    runner.invoke_sdk_method.assert_called_with("navigate", {"path": "Boss-Extrude1", "showEquations": True})
    call("inspect_topology", path="Boss-Extrude1/Face[0]", edges=True)
    runner.invoke_sdk_method.assert_called_with("getEdgeAnalysis", {"facePath": "Boss-Extrude1/Face[0]"})


def test_capture_viewport_modes(automation, runner):
    call("capture_viewport", view_name="*Top")
    automation.capture_viewport.assert_called_once_with(filepath=None, view_name="*Top")
    call("capture_viewport", mode="overview", save_dir="C:/out")
    runner.capture_views.assert_called_once_with(save_dir="C:/out")
    call("capture_viewport", mode="showcase", display_style="realistic", eye_mm=[1, 2, 3])
    automation.render_showcase_viewport.assert_called_once_with(
        eye_mm=[1, 2, 3], target_mm=None, up_vec=None, display_style="realistic", filepath=None)


def test_transaction_and_checkpoints(automation, monkeypatch):
    call("transaction")
    automation.transaction.assert_called_with(action="begin", name="MCP batch")
    call("transaction", action="abort", name="holes")
    automation.transaction.assert_called_with(action="abort", name="holes")

    manager = MagicMock()
    manager.create_checkpoint.return_value = {"success": True, "message": "made", "checkpoint": {}}
    manager.rollback_checkpoint.return_value = {"success": True, "message": "rolled"}
    monkeypatch.setattr(srv, "get_checkpoint_manager", lambda: manager)
    doc = object()
    automation.get_active_doc.return_value = (doc, None)
    assert call("transaction", action="checkpoint", name="base", description="note")["success"] is True
    manager.create_checkpoint.assert_called_once_with(doc=doc, name="base", description="note")
    assert call("transaction", action="rollback", name="base")["message"] == "rolled"
    manager.rollback_checkpoint.assert_called_once_with(doc=doc, name="base")
    assert call("transaction", action="rollback")["success"] is False


def test_manage_parameters(automation):
    call("manage_parameters")
    automation.get_parameters.assert_called_once_with()
    call("manage_parameters", action="add", name="Width", expression="100mm")
    automation.add_parameter.assert_called_once_with(name="Width", expression="100mm", units="mm")
    call("manage_parameters", action="set", name="Width", expression="120mm", units="inch")
    automation.set_parameter.assert_called_once_with(name="Width", expression="120mm", units="inch")
    assert call("manage_parameters", action="set", name="Width")["success"] is False
    assert automation.set_parameter.call_count == 1


def test_execute_code_languages(automation, runner, monkeypatch):
    seen = {}
    monkeypatch.setattr(srv, "_execute_python_fixed", lambda code, **kw: seen.update(code=code, **kw) or {"success": True})
    call("execute_code", language="python", code="x = 1", read_only=True, reset_session=True)
    assert seen == {"code": "x = 1", "user_confirmed_risks": False, "reset_session": True,
                    "read_only": True, "auto_transaction": True}

    call("execute_code", language="csharp", code="1+1", read_only=True, reset_session=True, timeout_ms=5000)
    runner.reset_session.assert_called_once_with()
    runner.execute_csharp.assert_called_once_with(code="1+1", read_only_only=True, timeout_ms=5000)

    call("execute_code", language="csharp_macro", code="class DynamicClass {}", parameters=[1, "a"])
    runner.execute_macro.assert_called_once_with(
        code="class DynamicClass {}", parameters=[1, "a"], read_only_only=False, timeout_ms=120000)


def test_get_capabilities_includes_installation(automation, monkeypatch):
    monkeypatch.setattr(srv.CapabilityInspector, "get_system_capabilities", staticmethod(lambda app: {"addins": []}))
    monkeypatch.setattr(srv, "get_solidworks_info", lambda: {"found": True, "version": "2025"})
    data = call("get_capabilities")["data"]
    assert data["addins"] == [] and data["installation"]["version"] == "2025"


def test_renamed_runner_tools(automation, runner):
    call("find_related_files", max_drawings_probed=10)
    runner.invoke_sdk_method.assert_called_with("findRelatedCadFiles", {"maxDrawingsProbed": 10})
    call("modify_feature_by_path", feature_path="Boss-Extrude1", depth_mm=25)
    assert runner.invoke_sdk_method.call_args.args[0] == "modifyFeatureParameterByPath"
    assert runner.invoke_sdk_method.call_args.args[1]["depthMm"] == 25


def test_old_names_explain_their_replacement():
    names = {t.name for t in srv._tool_definitions()}
    assert not set(srv._MERGED_TOOLS) & names
    for old, hint in srv._MERGED_TOOLS.items():
        message = srv._validate_tool_arguments(old, {})
        assert "merged" in message and hint in message
        assert hint.split()[0] in names, f"{old} points at a tool that does not exist"


def test_code_and_geometry_tools_are_tracked_as_mutating():
    assert {"execute_code", "manage_parameters", "modify_feature_by_path"} <= srv._PART_MUTATING_TOOLS
    assert srv._PART_MUTATING_TOOLS <= {t.name for t in srv._tool_definitions()}
