"""Studio state: dimension model, actions and viewport capture."""

import glob
import os
import tempfile

import pytest

from solidworks_mcp.ui import studio

RAW_DIMS = [
    {"full_name": "D1@Boss-Extrude1@Part1.Part", "name": "D1", "owner_feature": "Boss-Extrude1", "value": 4.8, "units": "mm"},
    {"full_name": "D1@Boss-Extrude2@Part1.Part", "name": "D1", "owner_feature": "Boss-Extrude2", "value": 50.0, "units": "mm"},
    {"full_name": "D2@Sketch3@Part1.Part", "name": "D2", "owner_feature": "Sketch3", "value": 45.0, "units": "deg"},
    {"full_name": "D9@Sketch3@Part1.Part", "name": "D9", "owner_feature": "Sketch3", "value": 7.0, "units": "mm", "driven": True},
]


class FakeAutomation:
    """Stands in for SolidWorksAutomation (no run_on_sta, so calls are direct)."""

    def __init__(self):
        self.set_calls = []
        self.fail_above = None
        self.views = []

    def capture_viewport(self, filepath=None, view_name="*Isometric"):
        self.views.append(view_name)
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


def _dims():
    return studio.build_dimension_model(
        RAW_DIMS,
        feature_labels={"Boss-Extrude1": "Hex Bolt Head"},
        dimension_labels={"D1@Boss-Extrude1@Part1.Part": "Head Thickness"},
    )


@pytest.fixture()
def automation():
    fake = FakeAutomation()
    studio.set_automation_instance(fake)
    studio.set_session("P.SLDPRT", _dims())
    yield fake
    studio.set_automation_instance(None)


def test_dimension_model_labels_groups_and_skips_driven():
    dims = _dims()
    assert len(dims) == 3  # the driven dimension is not adjustable
    head = dims["d0"]
    assert head["label"] == "Head Thickness" and head["feature"] == "Hex Bolt Head"
    assert head["min"] <= head["value"] <= head["max"] and head["step"] == 0.5
    angle = dims["d2"]
    assert angle["units"] == "deg" and angle["min"] == 0 and angle["max"] == 180 and angle["step"] == 1


def test_linear_sliders_never_reach_zero():
    # SolidWorks rejects non-positive linear dimensions.
    for value in (0.4, 1.0, 12.0, 300.0, 5000.0):
        bounds = studio.compute_slider_bounds(value)
        assert 0 < bounds["min"] <= value <= bounds["max"]


def test_change_summary_lists_only_changed_dimensions():
    dims = _dims()
    assert studio.build_change_summary(dims) == ""
    dims["d1"]["value"] = 65.0
    summary = studio.build_change_summary(dims)
    # The solidworks-interactive skill triggers on this opening sentence.
    assert summary.startswith("I have adjusted the SolidWorks model parameters via the interactive studio:")
    assert "D1@Boss-Extrude2@Part1.Part" in summary and "from 50 to 65 mm" in summary
    assert "Head Thickness" not in summary


def test_state_is_plain_json_for_the_view(automation):
    state = studio.get_session().state()
    assert [d["key"] for d in state["dims"]] == ["d0", "d1", "d2"]
    assert state["views"][0] == "*Isometric" and state["summary"] == ""
    assert set(state) == {"title", "info", "view", "views", "image", "status", "summary", "dims"}


def test_set_dimension_drives_solidworks_and_refreshes(automation):
    state = studio.handle_action("set_dimension", key="d1", value=60, view="*Front")
    assert automation.set_calls == [("D1@Boss-Extrude2@Part1.Part", 60.0, "mm")]
    assert state["dims"][1]["value"] == 60.0 and state["view"] == "*Front"
    assert automation.views == ["*Front"] and state["image"].startswith("data:image/png;base64,")
    assert state["info"].startswith("10 x 20 x 30 mm")
    assert "from 50 to 60 mm" in state["summary"]


def test_failed_rebuild_keeps_the_old_value(automation):
    automation.fail_above = 55
    state = studio.handle_action("set_dimension", key="d1", value=70)
    assert state["dims"][1]["value"] == 50.0
    assert "kept at 50" in state["status"] and "Rebuild failed" in state["status"]


def test_values_are_clamped_and_bad_input_ignored(automation):
    studio.handle_action("set_dimension", key="d1", value=10_000)
    assert automation.set_calls[-1][1] == studio.get_session().dims["d1"]["max"]
    calls = len(automation.set_calls)
    assert "no longer part" in studio.handle_action("set_dimension", key="zz", value=1)["status"]
    assert "non-numeric" in studio.handle_action("set_dimension", key="d1", value="abc")["status"]
    assert "non-numeric" in studio.handle_action("set_dimension", key="d1", value=float("nan"))["status"]
    assert len(automation.set_calls) == calls


def test_reset_restores_changed_dimensions_only(automation):
    studio.handle_action("set_dimension", key="d1", value=60)
    automation.set_calls.clear()
    state = studio.handle_action("reset")
    assert automation.set_calls == [("D1@Boss-Extrude2@Part1.Part", 50.0, "mm")]
    assert state["dims"][1]["value"] == 50.0 and state["summary"] == ""


def test_unknown_view_is_ignored(automation):
    state = studio.handle_action("capture", view="*Sideways")
    assert state["view"] == "*Isometric"


def test_viewport_captures_do_not_accumulate(automation):
    pattern = os.path.join(tempfile.gettempdir(), "swmcp_studio_*.png")
    before = set(glob.glob(pattern))
    for _ in range(3):
        assert studio.capture_viewport_data_uri().startswith("data:image/png;base64,")
    assert set(glob.glob(pattern)) == before
