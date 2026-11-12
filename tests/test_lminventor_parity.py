"""
Tests for lminventor parity features and universal volume/topology delta telemetry
in SolidWorks MCP.
"""

from unittest.mock import MagicMock, patch
import pytest

from solidworks_mcp.core.post_conditions import capture_vol_state, format_vol_delta
from solidworks_mcp.server import (
    _tool_definitions,
    _execute_tool_on_sta,
    _reload_api_handler,
    _execute_python_fixed,
    sw_automation,
)


class _DummyBody:
    def __init__(self, faces: int, edges: int):
        self._faces = faces
        self._edges = edges

    def GetFaceCount(self):
        return self._faces

    def GetEdgeCount(self):
        return self._edges


class _DummyPartDoc:
    def __init__(self, volume_mm3: float, faces: int = 6, edges: int = 12, bodies: int = 1):
        self._vol_m3 = volume_mm3 / 1e9
        self._bodies = [_DummyBody(faces, edges) for _ in range(bodies)]
        self.ActiveDoc = self

    def GetType(self):
        return 1  # swDocPART

    def GetTitle(self):
        return "TestPart.SLDPRT"

    def GetBodies2(self, body_type: int, visible_only: bool):
        return self._bodies

    def GetMassProperties(self):
        # [com_x, com_y, com_z, volume_m3, area_m2, mass_kg, ...]
        return [0.0, 0.0, 0.0, self._vol_m3, 0.01, 0.1]


def test_capture_and_format_vol_delta():
    """Verify capture_vol_state and format_vol_delta match lminventor's exact format."""
    doc_initial = _DummyPartDoc(volume_mm3=50000.0, faces=6, edges=12, bodies=1)
    state1 = capture_vol_state(doc_initial)
    assert state1 == (50000.0, 6, 12, 1)

    # First feature (before=None)
    suffix1, delta1 = format_vol_delta(None, state1)
    assert suffix1 == " | V 50000 mm³ | F6 E12"
    assert delta1["volume_before_mm3"] == 0.0
    assert delta1["volume_after_mm3"] == 50000.0
    assert delta1["volume_delta_mm3"] == 50000.0
    assert delta1["faces_after"] == 6
    assert delta1["edges_after"] == 12

    # Subsequent cut feature (-785 mm³)
    doc_after_cut = _DummyPartDoc(volume_mm3=49215.0, faces=7, edges=14, bodies=1)
    state2 = capture_vol_state(doc_after_cut)
    suffix2, delta2 = format_vol_delta(state1, state2)
    assert suffix2 == " | V 49215 (-785 mm³) | F7 E14"
    assert delta2["volume_before_mm3"] == 50000.0
    assert delta2["volume_after_mm3"] == 49215.0
    assert delta2["volume_delta_mm3"] == -785.0
    assert delta2["faces_before"] == 6
    assert delta2["faces_after"] == 7
    assert delta2["edges_before"] == 12
    assert delta2["edges_after"] == 14


def test_sta_wrapper_injects_volume_delta_and_air_cut_warning():
    """Verify _execute_tool_on_sta appends '| V ... (ΔV mm³) | F... E...' and warns on ΔV=0 cuts."""
    doc_before = _DummyPartDoc(volume_mm3=50000.0, faces=6, edges=12, bodies=1)
    doc_after = _DummyPartDoc(volume_mm3=50000.0, faces=6, edges=12, bodies=1)

    fake_app = MagicMock()
    fake_app.RevisionNumber = "33.5.0"
    # First access (pre-mutation) returns doc_before, second access (post-mutation) returns doc_after
    type(fake_app).ActiveDoc = property(lambda self: doc_after)

    with patch.object(sw_automation, "_connected", True), \
         patch.object(sw_automation, "_sw_app", fake_app), \
         patch("solidworks_mcp.server.find_blocking_modal_dialog", return_value=None), \
         patch("solidworks_mcp.server._dispatch_tool", return_value={
             "success": True,
             "message": "Cut extrude 10mm",
             "error_code": 0,
             "error_name": "swSuccess",
             "data": {"feature_name": "Cut-Extrude1"},
         }):
        res, sw_ver, act_doc, tid = _execute_tool_on_sta("cut_extrude", {"depth": 10.0})
        assert res["success"] is True
        assert " | V 50000 (+0 mm³) | F6 E12" in res["message"]
        assert "WARNING: ΔV=0 mm³" in res["message"]
        assert res["data"]["geometry_delta"]["volume_delta_mm3"] == 0.0
        assert res["data"]["zero_volume_change_warning"] is True


def test_all_lminventor_parity_tools_registered():
    """Verify all 25 new lminventor/mecAgent parity tools are present in _tool_definitions."""
    names = {t.name for t in _tool_definitions()}
    expected = {
        "draw_closed_profile",
        "hole_linear",
        "get_parameters",
        "set_parameter",
        "add_parameter",
        "list_faces",
        "list_edges",
        "find_face",
        "find_edge",
        "set_sheet_metal_thickness",
        "sheet_metal_face",
        "flange",
        "sheet_metal_cut",
        "delete_assembly_constraint",
        "manage_subassembly_kinematics",
        "inspect_assembly_kinematics",
        "drive_kinematic_motion",
        "manage_positional_representations",
        "apply_material_appearance",
        "render_showcase_viewport",
        "supervisor_clean_processes",
        "transaction",
        "reload_api",
        "sw_get_edge_analysis",
        "sw_modify_feature_parameter_by_path",
    }
    missing = expected - names
    assert not missing, f"Missing tools: {missing}"


def test_supervisor_clean_processes_dry_run():
    """Verify supervisor_clean_processes(confirm=False) performs a non-destructive audit."""
    res = sw_automation.supervisor_clean_processes(confirm=False)
    assert res["success"] is True
    assert res["data"]["confirm"] is False
    assert "terminated_pids" in res["data"]
    assert res["data"]["terminated_pids"] == []


def test_reload_api_handler():
    """Verify reload_api hot-reloads solidworks_mcp submodules."""
    res = _reload_api_handler()
    assert res["success"] is True
    assert len(res["data"]["reloaded_modules"]) > 0


def test_stateful_execute_python_and_final_answer():
    """Verify execute_python persists variables across calls, supports FinalAnswer, and resets."""
    with patch.object(sw_automation, "_connected", True), \
         patch.object(sw_automation, "_sw_app", MagicMock()):
        r1 = _execute_python_fixed(
            "my_persisted_var = 42",
            user_confirmed_risks=True,
            reset_session=True,
            auto_transaction=False,
        )
        assert r1["success"] is True

        r2 = _execute_python_fixed(
            "FinalAnswer(my_persisted_var * 2)",
            user_confirmed_risks=True,
            reset_session=False,
            auto_transaction=False,
        )
        assert r2["success"] is True
        assert r2["data"]["final_answer_called"] is True
        assert r2["data"]["result"] == "84"
