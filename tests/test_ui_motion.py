"""Motion scrubber: moves, collision checks, sweeps and the server tools."""

import math

import pytest

from solidworks_mcp import server as srv
from solidworks_mcp.constants import SwDocumentTypes
from solidworks_mcp.ui import motion


class Param:
    def __init__(self, owner):
        self.owner = owner

    @property
    def SystemValue(self):
        return self.owner.value_si


class Doc:
    def __init__(self, owner):
        self.owner = owner
        self.rebuilds = 0

    def GetTitle(self):
        return "Hinge.SLDASM"

    def GetType(self):
        return int(SwDocumentTypes.swDocASSEMBLY)

    def Parameter(self, name):
        return Param(self.owner) if name == "D1@Angle1" and self.owner.readable else None

    def EditRebuild3(self):
        self.rebuilds += 1

    def GraphicsRedraw2(self):
        pass


class FakeAutomation:
    """An 'Angle1' hinge: the lid hits the stop above 100 degrees; 170+ cannot be reached."""

    def __init__(self):
        self.angle = 30.0
        self.readable = True
        self.doc = Doc(self)
        self.set_calls = []

    @property
    def value_si(self):
        return math.radians(self.angle)

    def get_active_doc(self):
        return self.doc, None

    def set_dimension(self, name, value, units=None):
        self.set_calls.append((name, round(value, 4), units))
        if name != "D1@Angle1":
            return {"success": False, "message": "Dimension not found"}
        if value >= 170:
            return {"success": False, "message": "Mate could not be solved"}
        self.angle = value
        return {"success": True}

    def check_interference(self, treat_coincidence_as_interference=False):
        hits = []
        if self.angle > 100:
            hits = [{"index": 0, "volume_mm3": round(self.angle - 100, 3),
                     "components": ["Lid-1", "Stop-1"], "box_mm": None}]
        return {"success": True, "data": {"interference_count": len(hits), "interferences": hits}}

    def capture_viewport(self, filepath=None, view_name="*Isometric"):
        with open(filepath, "wb") as f:
            f.write(b"\x89PNG " + view_name.encode())
        return {"success": True, "data": {"filepath": filepath}}

    def _result(self, ok, msg, code, data=None):
        return {"success": ok, "message": msg, "data": data}


@pytest.fixture()
def hinge(monkeypatch):
    fake = FakeAutomation()
    motion.set_automation_instance(fake)
    monkeypatch.setattr(srv, "sw_automation", fake)
    yield fake
    motion.set_automation_instance(None)
    motion._session.__init__()


def _open(**kw):
    args = dict(constraint="Angle1", min_value=0, max_value=180, unit="deg")
    args.update(kw)
    return motion.open_scrubber(**args)


def test_open_reads_the_current_value(hinge):
    res = _open()
    assert res["success"], res
    s = res["data"]
    assert s["dimension"] == "D1@Angle1" and s["assembly"] == "Hinge.SLDASM"
    assert s["value"] == pytest.approx(30.0) and s["start_value"] == pytest.approx(30.0)
    assert s["step"] == 1.0 and s["interferences"] == []
    assert s["image"].startswith("data:image/png;base64,")
    assert hinge.set_calls == [], "opening must not move the mechanism when its value can be read"


def test_open_moves_to_the_low_end_when_the_value_cannot_be_read(hinge):
    hinge.readable = False
    s = _open(min_value=10)["data"]
    assert s["value"] == 10 and hinge.angle == 10


def test_open_validates_the_range(hinge):
    assert not _open(min_value=90, max_value=10)["success"]
    assert not motion.open_scrubber(constraint="", min_value=0, max_value=1)["success"]


def test_move_reports_collisions_and_clamps(hinge):
    _open()
    s = motion.handle_action("move", value=120)
    assert hinge.angle == 120 and s["value"] == 120
    assert s["interferences"][0]["components"] == ["Lid-1", "Stop-1"]
    assert "1 interference" in s["status"]
    s = motion.handle_action("move", value=-50)
    assert s["value"] == 0 and s["interferences"] == []


def test_unreachable_move_keeps_the_last_position(hinge):
    _open()
    s = motion.handle_action("move", value=175)
    assert s["value"] == pytest.approx(30.0) and "Could not move" in s["status"]


def test_sweep_builds_a_clash_map_and_returns_to_the_position(hinge):
    _open()
    motion.handle_action("move", value=45)
    s = motion.handle_action("sweep", steps=6)  # 0, 30, 60, 90, 120, 150, 180
    values = [p["value"] for p in s["sweep"]]
    assert values == [0, 30, 60, 90, 120, 150, 180]
    assert [p["count"] for p in s["sweep"]] == [0, 0, 0, 0, 1, 1, None]
    assert s["sweep"][4]["pairs"] == [["Lid-1", "Stop-1"]]
    assert hinge.angle == 45 and s["value"] == 45
    assert "2 collide" in s["status"] and "1 could not be reached" in s["status"]
    ranges = motion.clash_ranges(s["sweep"])
    assert ranges == [{"from": 120, "to": 150, "pairs": [["Lid-1", "Stop-1"]]}]
    assert "Collision from 120 to 150 deg: Lid-1 <-> Stop-1" in s["summary"]


def test_restore_and_collision_toggle(hinge):
    _open()
    motion.handle_action("move", value=130)
    s = motion.handle_action("set_check", check_collisions=False)
    assert s["check_collisions"] is False
    s = motion.handle_action("restore")
    assert hinge.angle == pytest.approx(30.0) and "starting position" in s["status"]
    assert s["interferences"] == []


def test_server_tools(hinge):
    res = srv._dispatch_tool("show_motion_scrubber",
                             {"constraint_name": "Angle1", "min_value": 0, "max_value": 180, "unit": "deg"})
    assert res["success"], res
    assert res["data"]["motion"]["value"] == pytest.approx(30.0)
    res = srv._dispatch_tool("motion_action", {"action": "move", "value": 60})
    assert res["success"] and res["data"]["motion"]["value"] == 60
    assert srv._dispatch_tool("motion_action", {"action": "fly"})["success"] is False


def test_scrubber_needs_an_assembly(hinge, monkeypatch):
    monkeypatch.setattr(Doc, "GetType", lambda self: int(SwDocumentTypes.swDocPART))
    res = srv._dispatch_tool("show_motion_scrubber",
                             {"constraint_name": "Angle1", "min_value": 0, "max_value": 1, "unit": "deg"})
    assert res["success"] is False and "assembly" in res["message"]


def test_sweep_gets_the_long_timeout():
    class Cfg:
        simulation_timeout = 600.0
        default_timeout = 60.0
    assert srv.get_tool_timeout("motion_action", Cfg(), {"action": "sweep"}) == 600.0
    assert srv.get_tool_timeout("motion_action", Cfg(), {"action": "move"}) == 60.0
