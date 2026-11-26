"""
SolidWorks Motion Scrubber - MCP App state
------------------------------------------
Backs ui://solidworks/motion.html. show_motion_scrubber takes one driving mate
dimension of the active assembly and its range; the view drags it through that
range. Each move sets the dimension in SolidWorks, rebuilds, captures the viewport
and (optionally) runs interference detection. A sweep steps across the whole range
and returns a clash map (which positions collide, and which components).

structuredContent.motion:
    {
      "assembly", "constraint", "dimension", "unit", "min", "max", "step",
      "value", "start_value", "view", "views", "image", "status",
      "check_collisions": bool,
      "interferences": [{"components", "volume_mm3", "box_mm"}],   # at the current value
      "sweep": [{"value", "count", "pairs", "volume_mm3"}] | null,
      "summary"
    }
"""

import logging
import math
import threading
from typing import Any, Dict, List, Optional

from . import studio

logger = logging.getLogger("SolidWorksMCP.Motion")

RESOURCE_URI = "ui://solidworks/motion.html"
VIEW_NAMES = studio.VIEW_NAMES
MAX_SWEEP_STEPS = 60

_automation_instance: Any = None
_lock = threading.Lock()


def _fmt(v: Optional[float]) -> str:
    return "n/a" if v is None else f"{v:.3f}".rstrip("0").rstrip(".")


class MotionSession:
    def __init__(self) -> None:
        self.assembly = ""
        self.constraint = ""
        self.dimension = ""
        self.unit = "mm"
        self.min = 0.0
        self.max = 0.0
        self.step = 1.0
        self.value: Optional[float] = None
        self.start_value: Optional[float] = None
        self.view = "*Isometric"
        self.image = ""
        self.status = ""
        self.check_collisions = True
        self.interferences: List[Dict[str, Any]] = []
        self.sweep: Optional[List[Dict[str, Any]]] = None

    def state(self) -> Dict[str, Any]:
        return {
            "assembly": self.assembly,
            "constraint": self.constraint,
            "dimension": self.dimension,
            "unit": self.unit,
            "min": self.min,
            "max": self.max,
            "step": self.step,
            "value": self.value,
            "start_value": self.start_value,
            "view": self.view,
            "views": list(VIEW_NAMES),
            "image": self.image,
            "status": self.status,
            "check_collisions": self.check_collisions,
            "interferences": list(self.interferences),
            "sweep": self.sweep,
            "summary": build_summary(self),
        }


_session = MotionSession()


def get_session() -> MotionSession:
    return _session


def set_automation_instance(instance: Any) -> None:
    global _automation_instance
    _automation_instance = instance
    studio.set_automation_instance(instance)


def render_page() -> str:
    from .apps import render
    return render(RESOURCE_URI)


def clash_ranges(sweep: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Merge consecutive colliding sweep positions into ranges: [{"from", "to", "pairs"}]."""
    ranges: List[Dict[str, Any]] = []
    for s in sweep or []:
        if s.get("count"):
            if ranges and ranges[-1]["open"]:
                ranges[-1]["to"] = s["value"]
                for p in s.get("pairs", []):
                    if p not in ranges[-1]["pairs"]:
                        ranges[-1]["pairs"].append(p)
            else:
                ranges.append({"from": s["value"], "to": s["value"], "pairs": list(s.get("pairs", [])), "open": True})
        elif ranges:
            ranges[-1]["open"] = False
    return [{k: v for k, v in r.items() if k != "open"} for r in ranges]


def build_summary(s: MotionSession) -> str:
    if not s.constraint:
        return ""
    lines = [f"Motion check of '{s.constraint}' in {s.assembly or 'the assembly'} "
             f"over {_fmt(s.min)}..{_fmt(s.max)} {s.unit}:"]
    if s.sweep:
        ranges = clash_ranges(s.sweep)
        if ranges:
            for r in ranges:
                pairs = "; ".join(" <-> ".join(p) for p in r["pairs"]) or "components unknown"
                lines.append(f"- Collision from {_fmt(r['from'])} to {_fmt(r['to'])} {s.unit}: {pairs}")
        else:
            lines.append(f"- No collisions at any of the {len(s.sweep)} positions checked.")
    if s.value is not None:
        where = f"- Currently at {_fmt(s.value)} {s.unit}"
        if s.check_collisions:
            where += (f", {len(s.interferences)} interference(s)" if s.interferences else ", no interference")
        lines.append(where + ".")
    lines.append("Please review the clearances against the design requirements and suggest fixes for any collision.")
    return "\n".join(lines)


# ============================================================================
# SolidWorks access
# ============================================================================

def _auto():
    if _automation_instance is None:
        raise RuntimeError("SolidWorks automation is not available")
    return _automation_instance


def _dimension_name(constraint: str) -> str:
    return constraint if "@" in constraint else f"D1@{constraint}"


def _read_value(dimension: str, unit: str) -> Optional[float]:
    """Current value of a mate dimension in `unit`, read from IDimension.SystemValue (m / rad)."""
    try:
        doc, err = _auto().get_active_doc()
        if err or doc is None:
            return None
        param = doc.Parameter(dimension)
        if param is None:
            return None
        system = float(getattr(param, "SystemValue"))
    except Exception as e:
        logger.debug(f"motion: cannot read {dimension}: {e}")
        return None
    factors = {"mm": 1000.0, "cm": 100.0, "m": 1.0, "inch": 1000.0 / 25.4, "deg": 180.0 / math.pi}
    return round(system * factors.get(unit, 1.0), 6)  # 0.5236 rad -> 30.0, not 29.999999999999996


def _set_value(value: float) -> Dict[str, Any]:
    """Set the driving dimension (D1@Mate, falling back to the plain name) and rebuild."""
    automation = _auto()
    res = automation.set_dimension(name=_session.dimension, value=value, units=_session.unit)
    if not (isinstance(res, dict) and res.get("success")) and "@" not in _session.constraint:
        alt = automation.set_dimension(name=_session.constraint, value=value, units=_session.unit)
        if isinstance(alt, dict) and alt.get("success"):
            res = alt
    try:
        doc, err = automation.get_active_doc()
        if not err and doc is not None:
            doc.EditRebuild3()
            doc.GraphicsRedraw2()
    except Exception:
        pass
    return res if isinstance(res, dict) else {"success": False, "message": "set_dimension failed"}


def _interferences() -> List[Dict[str, Any]]:
    res = _auto().check_interference(treat_coincidence_as_interference=False)
    if not isinstance(res, dict) or not res.get("success"):
        return []
    data = res.get("data") or {}
    return [
        {"components": list(i.get("components") or []), "volume_mm3": i.get("volume_mm3"), "box_mm": i.get("box_mm")}
        for i in data.get("interferences") or []
    ]


def _pairs(hits: List[Dict[str, Any]]) -> List[List[str]]:
    out: List[List[str]] = []
    for h in hits:
        pair = sorted(h.get("components") or [])
        if pair and pair not in out:
            out.append(pair)
    return out


def _clamp(value: float) -> float:
    return min(max(value, _session.min), _session.max)


def _move(value: float) -> bool:
    res = _set_value(value)
    if not res.get("success"):
        _session.status = f"Could not move to {_fmt(value)} {_session.unit}: {res.get('message', 'rebuild failed')}"
        return False
    _session.value = value
    _session.interferences = _interferences() if _session.check_collisions else []
    _session.image = studio.capture_viewport_data_uri(_session.view) or _session.image
    hits = len(_session.interferences)
    _session.status = f"At {_fmt(value)} {_session.unit}" + (
        f": {hits} interference(s)." if hits else (": no interference." if _session.check_collisions else ".")
    )
    return True


def _valid_number(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def open_scrubber(constraint: str, min_value: Any, max_value: Any, unit: str = "mm", step: Any = None,
                  view: str = "*Isometric", check_collisions: bool = True) -> Dict[str, Any]:
    """Set up the scrubber for one driving dimension. Returns {success, message, data}."""
    lo, hi = _valid_number(min_value), _valid_number(max_value)
    if not constraint:
        return {"success": False, "message": "constraint_name is required.", "data": None}
    if lo is None or hi is None or hi <= lo:
        return {"success": False, "message": "min_value and max_value must be numbers with max_value > min_value.", "data": None}
    with _lock:
        s = _session
        s.__init__()
        automation = _auto()
        doc, err = automation.get_active_doc()
        if err:
            return {"success": False, "message": err.get("message", "No active document."), "data": None}
        try:
            title = getattr(doc, "GetTitle", "")
            s.assembly = str(title() if callable(title) else title)
        except Exception:
            s.assembly = ""
        s.constraint = constraint
        s.dimension = _dimension_name(constraint)
        s.unit = unit if unit in ("mm", "deg", "inch", "m", "cm") else "mm"
        s.min, s.max = lo, hi
        st = _valid_number(step)
        s.step = st if st and st > 0 else (1.0 if s.unit == "deg" else max(round((hi - lo) / 100.0, 3), 0.001))
        s.view = view if view in VIEW_NAMES else "*Isometric"
        s.check_collisions = bool(check_collisions)

        current = _read_value(s.dimension, s.unit)
        if current is None:
            # The value could not be read: start at the low end, which also proves the dimension can be driven.
            if not _move(lo):
                return {"success": False, "message": s.status, "data": None}
            s.start_value = lo
        else:
            s.start_value = current
            s.value = current
            s.interferences = _interferences() if s.check_collisions else []
            s.image = studio.capture_viewport_data_uri(s.view)
            s.status = f"At {_fmt(current)} {s.unit}. Drag to move the mechanism."
        return {"success": True, "message": "", "data": s.state()}


def handle_action(action: str, value: Any = None, view: Any = None, steps: Any = None,
                  check_collisions: Any = None) -> Dict[str, Any]:
    """Run one scrubber action (move, sweep, capture, restore, set_check) and return the new state."""
    with _lock:
        s = _session
        if view in VIEW_NAMES:
            s.view = view
        if check_collisions is not None:
            s.check_collisions = bool(check_collisions)

        if action == "move":
            v = _valid_number(value)
            if v is None:
                s.status = "Ignored a non-numeric position."
            else:
                _move(_clamp(v))
        elif action == "restore":
            if s.start_value is not None and _move(s.start_value):
                s.status = f"Back at the starting position ({_fmt(s.start_value)} {s.unit})."
        elif action == "capture":
            s.image = studio.capture_viewport_data_uri(s.view) or s.image
            s.status = f"Showing {s.view.lstrip('*')} view."
        elif action == "set_check":
            s.status = "Collision check on." if s.check_collisions else "Collision check off."
            if s.check_collisions and s.value is not None:
                s.interferences = _interferences()
        elif action == "sweep":
            n = int(_valid_number(steps) or 12)
            n = max(2, min(MAX_SWEEP_STEPS, n))
            back_to = s.value
            sweep = []
            for i in range(n + 1):
                v = s.min + (s.max - s.min) * i / n
                res = _set_value(v)
                if not res.get("success"):
                    sweep.append({"value": round(v, 4), "count": None, "pairs": [], "volume_mm3": 0,
                                  "error": res.get("message", "rebuild failed")})
                    continue
                hits = _interferences()
                sweep.append({"value": round(v, 4), "count": len(hits), "pairs": _pairs(hits),
                              "volume_mm3": round(sum(h.get("volume_mm3") or 0 for h in hits), 3)})
            s.sweep = sweep
            if back_to is not None:
                _move(back_to)
            clashes = [x for x in sweep if x.get("count")]
            failed = [x for x in sweep if x.get("count") is None]
            s.status = (f"Swept {len(sweep)} positions: "
                        + (f"{len(clashes)} collide" if clashes else "no collisions")
                        + (f", {len(failed)} could not be reached" if failed else "") + ".")
        else:
            s.status = f"Unknown motion action '{action}'."
        return s.state()
