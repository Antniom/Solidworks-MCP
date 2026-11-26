"""FEA results view: state, factor of safety, actions and the server tools."""

import pytest

from solidworks_mcp import server as srv
from solidworks_mcp.ui import fea


def _results(max_stress=120.0, max_disp=0.42, study="Static 1"):
    return {"success": True, "message": "ok", "data": {
        "study_name": study,
        "von_mises_stress_mpa": {"max": max_stress, "max_node": 17, "min": 0.01, "min_node": 3, "unit": "MPa"},
        "resultant_displacement_mm": {"max": max_disp, "max_node": 99, "min": 0.0, "min_node": 1, "unit": "mm"},
        "sanity_check": {"passed": True, "warnings": []},
    }}


class FakeAutomation:
    def __init__(self):
        self.results = _results()
        self.solves = []
        self.solve_ok = True
        self.material = ""

    def get_simulation_results(self, study_name=None):
        return self.results

    def mesh_and_run_simulation(self, element_size_mm=2.0, study_name=None, tolerance_mm=0.1):
        self.solves.append(element_size_mm)
        if not self.solve_ok:
            return {"success": False, "message": "Solver returned non-zero code: 3"}
        self.results = _results(max_stress=150.0, max_disp=0.5)
        return {"success": True, "data": {"element_count": 1200, "node_count": 2300, "element_size_mm": element_size_mm}}

    def capture_viewport(self, filepath=None, view_name="*Isometric"):
        with open(filepath, "wb") as f:
            f.write(b"\x89PNG plot")
        return {"success": True, "data": {"filepath": filepath}}

    def get_active_doc(self):
        class Doc:
            def GetTitle(self):
                return "Bracket.SLDPRT"
        return Doc(), None

    def _get_active_study(self, study_name=None):
        return False, "no COM here", None

    def _result(self, ok, msg, code, data=None):
        return {"success": ok, "message": msg, "data": data}


@pytest.fixture()
def automation(monkeypatch):
    fake = FakeAutomation()
    fea.set_automation_instance(fake)
    monkeypatch.setattr(srv, "sw_automation", fake)
    yield fake
    fea.set_automation_instance(None)
    fea._session.__init__()


def test_load_with_given_yield_computes_fos(automation):
    res = fea.load(yield_mpa=351.571)
    assert res["success"]
    state = res["data"]
    assert state["study"] == "Static 1" and state["model"] == "Bracket.SLDPRT"
    assert state["stress"]["max"] == 120.0 and state["displacement"]["max"] == 0.42
    assert state["yield_source"] == "given" and state["fos"] == pytest.approx(351.571 / 120.0, abs=1e-3)
    assert state["image"].startswith("data:image/png;base64,")
    assert state["summary"].startswith("FEA results for study 'Static 1'")
    assert "factor of safety" in state["summary"]


def test_unknown_yield_leaves_fos_empty(automation):
    state = fea.load()["data"]
    assert state["yield_mpa"] is None and state["fos"] is None


def test_library_yield_is_used_when_the_material_is_known(automation, monkeypatch):
    monkeypatch.setattr(fea, "_material_and_yield", lambda study: ("AISI 1020", 351.571, "library"))
    state = fea.load()["data"]
    assert state["material"] == "AISI 1020" and state["yield_source"] == "library"
    assert state["fos"] == pytest.approx(2.93, abs=0.01)


def test_unsolved_study_is_reported(automation):
    automation.results = {"success": False, "message": "No simulation results available. Run solver first."}
    res = fea.load()
    assert not res["success"] and "Run solver first" in res["message"]


def test_rerun_solves_and_refreshes(automation):
    fea.load(yield_mpa=300)
    state = fea.handle_action("rerun", element_size_mm=1.5)
    assert automation.solves == [1.5]
    assert state["stress"]["max"] == 150.0 and state["fos"] == 2.0
    assert state["mesh"] == {"elements": 1200, "nodes": 2300, "element_size_mm": 1.5}
    assert "Re-solved with 1.5 mm" in state["status"]
    # The next re-solve reuses the last element size.
    fea.handle_action("rerun")
    assert automation.solves == [1.5, 1.5]


def test_failed_rerun_keeps_previous_results(automation):
    fea.load(yield_mpa=300)
    automation.solve_ok = False
    state = fea.handle_action("rerun", element_size_mm=-4)
    assert automation.solves == [2.0]  # an invalid size falls back to the default
    assert state["stress"]["max"] == 120.0 and "Re-solve failed" in state["status"]


def test_set_yield_validates(automation):
    fea.load()
    assert fea.handle_action("set_yield", yield_mpa=240)["fos"] == 2.0
    assert "positive" in fea.handle_action("set_yield", yield_mpa=-1)["status"]
    assert fea.get_session().yield_mpa == 240


def test_server_tools(automation):
    res = srv._dispatch_tool("show_fea_results", {"yield_strength_mpa": 240})
    assert res["success"], res
    assert res["data"]["fea"]["fos"] == 2.0
    assert "FEA results view ready" in res["message"]

    res = srv._dispatch_tool("fea_action", {"action": "capture", "view": "*Front"})
    assert res["success"] and res["data"]["fea"]["view"] == "*Front"
    assert srv._dispatch_tool("fea_action", {"action": "explode"})["success"] is False


def test_fea_action_needs_an_open_view(automation):
    assert srv._dispatch_tool("fea_action", {"action": "capture"})["success"] is False


def test_rerun_gets_the_simulation_timeout():
    class Cfg:
        simulation_timeout = 600.0
        default_timeout = 60.0
    assert srv.get_tool_timeout("fea_action", Cfg(), {"action": "rerun"}) == 600.0
    assert srv.get_tool_timeout("fea_action", Cfg(), {"action": "capture"}) == 60.0
