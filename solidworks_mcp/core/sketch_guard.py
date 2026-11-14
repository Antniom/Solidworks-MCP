"""
Exact sketch-entity creation for SolidWorks.

SolidWorks snaps new sketch points onto nearby sketch points and model edges ("inference")
with a tolerance measured in screen pixels. Through the API this silently moved circle
centres and rectangle corners by up to several millimetres, changed radii, merged corners
(CreateXxx then returns nothing) and produced open profiles (see DINA MCP-007/013/015).

`direct_sketch(doc)` sets ISketchManager.AddToDB = True for the duration of the calls, which
writes entities straight to the database with no snapping or inference, and turns off
DisplayWhenAdded for speed. The read-back helpers let each tool compare what was created
with what was requested instead of echoing the request.
"""

import contextlib
import logging
import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .com_compat import get_attr_or_call

logger = logging.getLogger(__name__)

# Accept read-back differences below this (metres). SolidWorks stores doubles; with AddToDB
# the values come back exact to ~1e-12.
TOL_M = 1e-7


@contextlib.contextmanager
def direct_sketch(doc: Any):
    """Create sketch entities with no snapping/inference (AddToDB) and restore the settings afterwards."""
    sm = getattr(doc, "SketchManager", None)
    prev_db = prev_disp = None
    try:
        if sm is not None:
            try:
                prev_db = bool(sm.AddToDB)
                prev_disp = bool(sm.DisplayWhenAdded)
            except Exception:
                prev_db, prev_disp = False, True
            sm.AddToDB = True
            sm.DisplayWhenAdded = False
    except Exception as e:  # never block drawing because of the guard itself
        logger.debug(f"direct_sketch: could not set AddToDB: {e}")
    try:
        yield sm
    finally:
        if sm is not None:
            try:
                sm.AddToDB = bool(prev_db) if prev_db is not None else False
                sm.DisplayWhenAdded = bool(prev_disp) if prev_disp is not None else True
            except Exception:
                pass
        try:
            doc.GraphicsRedraw2()
        except Exception:
            pass


def _pt(p: Any) -> Optional[Tuple[float, float]]:
    if p is None:
        return None
    try:
        return float(get_attr_or_call(p, "X")), float(get_attr_or_call(p, "Y"))
    except Exception:
        return None


def line_points(seg: Any) -> Optional[Tuple[Tuple[float, float], Tuple[float, float]]]:
    """(start, end) of a sketch line in sketch coordinates (m)."""
    try:
        a = _pt(get_attr_or_call(seg, "GetStartPoint2"))
        b = _pt(get_attr_or_call(seg, "GetEndPoint2"))
        if a and b:
            return a, b
    except Exception:
        pass
    return None


def arc_center_radius(seg: Any) -> Optional[Tuple[Tuple[float, float], float]]:
    """(centre, radius) of a sketch arc/circle in sketch coordinates (m)."""
    try:
        c = _pt(get_attr_or_call(seg, "GetCenterPoint2"))
        r = get_attr_or_call(seg, "GetRadius")
        if c is not None and r is not None:
            return c, float(r)
    except Exception:
        pass
    return None


def close(a: Tuple[float, float], b: Tuple[float, float], tol: float = TOL_M) -> bool:
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def delete_segments(doc: Any, segments: Iterable[Any]) -> None:
    """Remove sketch segments that were created wrong, so a failed call leaves no debris."""
    try:
        doc.ClearSelection2(True)
        any_sel = False
        for s in segments:
            if s is None:
                continue
            try:
                any_sel = bool(s.Select4(True, None)) or any_sel
            except Exception:
                try:
                    any_sel = bool(s.Select(True)) or any_sel
                except Exception:
                    pass
        if any_sel:
            doc.EditDelete()
    except Exception as e:
        logger.debug(f"delete_segments failed: {e}")


def sketch_frame(doc: Any) -> Optional[Dict[str, Any]]:
    """
    Where the active sketch sits in the model: origin and x/y/normal axes in world coordinates (mm
    for the origin). Sketch tools take sketch-local x/y; on a face whose normal is -Z the sketch
    x-axis is world -X, which silently mirrored geometry before this was reported (MCP-018).
    """
    try:
        sk = get_attr_or_call(doc.SketchManager, "ActiveSketch")
        if sk is None:
            return None
        xf = get_attr_or_call(sk, "ModelToSketchTransform")
        a = [float(v) for v in get_attr_or_call(xf, "ArrayData")]
        s = a[12] if len(a) > 12 and a[12] else 1.0
        # model->sketch (row vectors): p_s = s * (p_m . R) + T  =>  p_m = ((p_s - T) / s) . R^T
        # so world images of the sketch axes are the COLUMNS of R.
        x_axis = [a[0], a[3], a[6]]
        y_axis = [a[1], a[4], a[7]]
        normal = [a[2], a[5], a[8]]
        t = [a[9], a[10], a[11]]
        origin = [-(t[0] * a[0] + t[1] * a[1] + t[2] * a[2]) / s,
                  -(t[0] * a[3] + t[1] * a[4] + t[2] * a[5]) / s,
                  -(t[0] * a[6] + t[1] * a[7] + t[2] * a[8]) / s]
        r = lambda v: [round(c, 6) + 0.0 for c in v]  # noqa: E731  (+0.0 turns -0.0 into 0.0)
        return {
            "origin_mm": [round(c * 1000.0, 4) + 0.0 for c in origin],
            "x_axis_world": r(x_axis),
            "y_axis_world": r(y_axis),
            "normal_world": r(normal),
        }
    except Exception as e:
        logger.debug(f"sketch_frame failed: {e}")
        return None


def sketch_to_world_mm(frame: Dict[str, Any], x_mm: float, y_mm: float) -> List[float]:
    """World position (mm) of a sketch-local point (mm) for a frame from sketch_frame()."""
    o, ex, ey = frame["origin_mm"], frame["x_axis_world"], frame["y_axis_world"]
    return [round(o[i] + x_mm * ex[i] + y_mm * ey[i], 4) + 0.0 for i in range(3)]


def axis_note(frame: Optional[Dict[str, Any]]) -> str:
    """Human note when the sketch axes are not the obvious world axes."""
    if not frame:
        return ""
    names = {(1, 0, 0): "+X", (-1, 0, 0): "-X", (0, 1, 0): "+Y", (0, -1, 0): "-Y", (0, 0, 1): "+Z", (0, 0, -1): "-Z"}
    def nm(v):
        key = tuple(int(round(c)) if abs(abs(c) - 1) < 1e-6 or abs(c) < 1e-6 else 9 for c in v)
        return names.get(key, "[" + ", ".join(f"{c:.3f}" for c in v) + "]")
    return (f"sketch x = world {nm(frame['x_axis_world'])}, sketch y = world {nm(frame['y_axis_world'])}, "
            f"normal = world {nm(frame['normal_world'])}")


def mm(v: float) -> float:
    return round(v * 1000.0, 4) + 0.0


def hypot(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])
