"""
SolidWorks Interactive Studio - MCP App state
---------------------------------------------
The studio is an MCP App (https://github.com/modelcontextprotocol/ext-apps): a
self-contained HTML view (studio.html) served as the ui://solidworks/studio.html
resource. The host renders it next to the show_interactive_studio result and
forwards the result's structuredContent to it. User actions (slider moves, view
changes, reset) come back as calls to the app-only studio_action tool.

This module owns the studio state and turns it into the plain JSON the view draws:

    {
      "title", "info", "view", "views", "image", "status", "summary",
      "dims": [{"key", "name", "label", "feature", "value", "original",
                "units", "min", "max", "step"}, ...]
    }
"""

import base64
import logging
import math
import os
import tempfile
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger("SolidWorksMCP.Studio")

VIEW_NAMES = ["*Isometric", "*Front", "*Top", "*Right", "*Back", "*Left", "*Bottom"]
READY_STATUS = "Drag a slider to rebuild the model in SolidWorks."

_automation_instance: Any = None
_action_lock = threading.Lock()


# ============================================================================
# Dimension helpers
# ============================================================================

def infer_dimension_semantic_hint(dim: Dict[str, Any]) -> str:
    """Infer a human-readable meaning for default CAD dimension names (D1, D2, ...)."""
    name = str(dim.get("name", "")).lower()
    owner = str(dim.get("owner_feature", "")).lower()
    dim_type = str(dim.get("type", "")).lower()
    units = str(dim.get("units", "mm")).lower()

    if "deg" in units or "rad" in units or "angular" in dim_type or dim.get("is_angle"):
        return "Angle"
    if "fillet" in owner or "fillet" in name:
        return "Fillet Radius"
    if "chamfer" in owner or "chamfer" in name:
        return "Chamfer Distance"
    if "cut" in owner:
        return "Cut Depth" if name in ("d1", "depth") else "Cut Dimension"
    if "revolve" in owner:
        return "Revolve Angle"
    if "hole" in owner or "wizard" in owner:
        return "Hole Diameter" if ("dia" in name or "d1" in name) else "Hole Depth"
    if "boss" in owner or "extrude" in owner or "extrusion" in owner:
        return "Extrusion Depth" if name == "d1" else "Extrusion Dimension"
    if "sketch" in owner:
        if "dia" in name or "rad" in name:
            return "Diameter / Radius"
        return "Sketch Dimension"
    return "Dimension"


def is_angle_dimension(dim: Dict[str, Any]) -> bool:
    units = str(dim.get("units", "mm")).lower()
    dim_type = str(dim.get("type", "")).lower()
    return (
        "deg" in units
        or "rad" in units
        or "angular" in dim_type
        or dim.get("type_code") in (1, 3, 16)
        or bool(dim.get("is_angle"))
    )


def compute_slider_bounds(value: float, is_angle: bool = False) -> Dict[str, float]:
    """
    Slider range and step for a value: {"min", "max", "step"}.

    Ranges are clean engineering brackets that grow with the value. Linear minimums stay
    above zero because SolidWorks rejects non-positive linear dimensions.
    """
    if is_angle:
        return {"min": 0.0, "max": 360.0 if abs(value) > 180.0 else 180.0, "step": 1.0}
    v = abs(value)
    for limit, low, high, step in (
        (15.0, 0.5, 30.0, 0.5),
        (30.0, 1.0, 60.0, 0.5),
        (60.0, 2.0, 120.0, 1.0),
        (120.0, 5.0, 250.0, 1.0),
        (250.0, 10.0, 500.0, 2.0),
        (500.0, 20.0, 1000.0, 5.0),
    ):
        if v <= limit:
            return {"min": min(low, v) or low, "max": high, "step": step}
    return {"min": 50.0, "max": float(math.ceil(v * 2.0 / 50.0) * 50.0), "step": 5.0}


def _dimension_entry(
    dim: Dict[str, Any],
    feature_labels: Dict[str, str],
    dimension_labels: Dict[str, str],
) -> Optional[Dict[str, Any]]:
    try:
        value = float(dim.get("value"))
    except (TypeError, ValueError):
        return None
    if dim.get("driven") or dim.get("read_only"):
        return None

    full_name = str(dim.get("full_name") or dim.get("name") or "")
    if not full_name:
        return None
    angle = is_angle_dimension(dim)
    owner = str(dim.get("owner_feature") or "")
    bounds = compute_slider_bounds(value, angle)
    return {
        "name": full_name,
        "label": dimension_labels.get(full_name) or infer_dimension_semantic_hint(dim),
        "feature": feature_labels.get(owner, owner) or "Model",
        "value": value,
        "original": value,
        "units": "deg" if angle else str(dim.get("units") or "mm"),
        "min": bounds["min"],
        "max": bounds["max"],
        "step": bounds["step"],
    }


def build_dimension_model(
    dimensions: Any,
    feature_labels: Optional[Dict[str, str]] = None,
    dimension_labels: Optional[Dict[str, str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Convert get_dimensions() output into {"d0": {...}, "d1": {...}} keyed slider entries."""
    if isinstance(dimensions, dict):
        dimensions = dimensions.get("dimensions", [])
    if not isinstance(dimensions, list):
        dimensions = []
    out: Dict[str, Dict[str, Any]] = {}
    for dim in dimensions:
        if not isinstance(dim, dict):
            continue
        entry = _dimension_entry(dim, feature_labels or {}, dimension_labels or {})
        if entry is not None:
            out[f"d{len(out)}"] = entry
    return out


def _format_number(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def build_change_summary(dims: Dict[str, Dict[str, Any]]) -> str:
    """Prompt text describing every dimension that differs from its original value."""
    lines = [
        f"- {d['label']} ({d['name']}) on {d['feature']}: "
        f"changed from {_format_number(d['original'])} to {_format_number(d['value'])} {d['units']}"
        for d in dims.values()
        if abs(d["value"] - d["original"]) > 1e-6
    ]
    if not lines:
        return ""
    # The solidworks-interactive skill triggers on this opening sentence: keep it unchanged.
    return (
        "I have adjusted the SolidWorks model parameters via the interactive studio:\n"
        + "\n".join(lines)
        + "\nPlease verify the model geometry and update any associated technical drawings or stress simulations."
    )


def describe_model(bbox: Optional[Dict[str, Any]], dim_count: int) -> str:
    """One-line model summary shown next to the title."""
    parts = []
    if isinstance(bbox, dict):
        try:
            parts.append(
                " x ".join(_format_number(float(bbox[k])) for k in ("dx", "dy", "dz")) + " mm"
            )
        except (KeyError, TypeError, ValueError):
            pass
    parts.append(f"{dim_count} adjustable dimension{'s' if dim_count != 1 else ''}")
    return " | ".join(parts)


# ============================================================================
# Session state
# ============================================================================

class StudioSession:
    """State of the studio currently on display."""

    def __init__(self) -> None:
        self.title = "SolidWorks Model"
        self.dims: Dict[str, Dict[str, Any]] = {}
        self.image = ""
        self.bbox: Optional[Dict[str, Any]] = None
        self.view = "*Isometric"
        self.status = "Open a model and call show_interactive_studio."

    def state(self) -> Dict[str, Any]:
        """The JSON the studio view draws (sent as structuredContent.studio)."""
        return {
            "title": self.title or "SolidWorks Model",
            "info": describe_model(self.bbox, len(self.dims)),
            "view": self.view,
            "views": list(VIEW_NAMES),
            "image": self.image or "",
            "status": self.status,
            "summary": build_change_summary(self.dims),
            "dims": [{"key": key, **dim} for key, dim in self.dims.items()],
        }


_session = StudioSession()


def get_session() -> StudioSession:
    return _session


def set_automation_instance(instance: Any) -> None:
    """Inject the automation object the studio should drive."""
    global _automation_instance
    _automation_instance = instance


def set_session(
    title: str,
    dims: Dict[str, Dict[str, Any]],
    image: str = "",
    bbox: Optional[Dict[str, Any]] = None,
    view: str = "*Isometric",
) -> StudioSession:
    """Replace the studio state (a new show_interactive_studio call)."""
    _session.title = title
    _session.dims = dims
    _session.image = image
    _session.bbox = bbox
    _session.view = view if view in VIEW_NAMES else "*Isometric"
    _session.status = READY_STATUS
    return _session


RESOURCE_URI = "ui://solidworks/studio.html"


def render_page() -> str:
    """The MCP App view: one self-contained HTML file, no network access needed."""
    from .apps import render
    return render(RESOURCE_URI)


# ============================================================================
# SolidWorks access (always through the STA worker)
# ============================================================================

def _call(func_name: str, **kwargs: Any) -> Any:
    automation = _automation_instance
    if automation is None:
        raise RuntimeError("SolidWorks automation is not available")
    target = getattr(automation, func_name)
    run_on_sta = getattr(automation, "run_on_sta", None)
    if callable(run_on_sta) and not _is_mock(automation):
        return run_on_sta(target, **kwargs)
    return target(**kwargs)


def _is_mock(obj: Any) -> bool:
    try:
        from unittest.mock import Mock
        return isinstance(obj, Mock)
    except Exception:
        return False


def capture_viewport_data_uri(view_name: str = "*Isometric") -> str:
    """Capture the viewport to a temporary file and return it as a data URI."""
    fd, path = tempfile.mkstemp(prefix="swmcp_studio_", suffix=".png")
    os.close(fd)
    try:
        res = _call("capture_viewport", filepath=path, view_name=view_name)
        if isinstance(res, dict) and res.get("success"):
            saved = (res.get("data") or {}).get("filepath") or path
            if os.path.exists(saved) and os.path.getsize(saved) > 0:
                with open(saved, "rb") as f:
                    return "data:image/png;base64," + base64.b64encode(f.read()).decode("ascii")
    except Exception as e:
        logger.warning(f"Studio viewport capture failed: {e}")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return ""


def model_bounds() -> Optional[Dict[str, Any]]:
    try:
        res = _call("inspect_model")
        if isinstance(res, dict) and res.get("success"):
            return (res.get("data") or {}).get("dimensions_mm")
    except Exception:
        pass
    return None


# ============================================================================
# Actions sent by the view through studio_action
# ============================================================================

def _refresh_view() -> None:
    image = capture_viewport_data_uri(_session.view)
    if image:
        _session.image = image
    bbox = model_bounds()
    if bbox:
        _session.bbox = bbox


def _apply_dimension(key: str, value: Any) -> bool:
    """Set one dimension in SolidWorks. Returns True when the model changed."""
    dim = _session.dims.get(key)
    if dim is None:
        _session.status = "That dimension is no longer part of this studio. Reopen the studio."
        return False
    try:
        target = float(value)
    except (TypeError, ValueError):
        _session.status = "Ignored a non-numeric value."
        return False
    if not math.isfinite(target):
        _session.status = "Ignored a non-numeric value."
        return False
    target = min(max(target, dim["min"]), dim["max"])

    previous = dim["value"]
    try:
        res = _call("set_dimension", name=dim["name"], value=target, units=dim["units"])
    except Exception as e:
        res = {"success": False, "message": str(e)}
    if not isinstance(res, dict) or not res.get("success"):
        reason = res.get("message", "rebuild failed") if isinstance(res, dict) else "rebuild failed"
        _session.status = f"{dim['label']} kept at {previous:g} {dim['units']}: {reason}"
        return False

    dim["value"] = target
    _session.status = f"{dim['label']} set to {target:g} {dim['units']}."
    return True


def handle_action(action: str, key: Any = None, value: Any = None, view: Any = None) -> Dict[str, Any]:
    """Run one studio action and return the full studio state that reflects its outcome."""
    with _action_lock:
        if view in VIEW_NAMES:
            _session.view = view
        if action == "set_dimension":
            if _apply_dimension(str(key or ""), value):
                _refresh_view()
        elif action == "capture":
            _session.status = f"Showing {_session.view.lstrip('*')} view."
            _refresh_view()
        elif action == "reset":
            changed, failed = False, []
            for k, dim in _session.dims.items():
                if abs(dim["value"] - dim["original"]) > 1e-9:
                    if _apply_dimension(k, dim["original"]):
                        changed = True
                    else:
                        failed.append(dim["label"])
            _session.status = (
                f"Could not restore: {', '.join(failed)}." if failed
                else "All dimensions restored to their original values."
            )
            if changed:
                _refresh_view()
        else:
            _session.status = f"Unknown studio action '{action}'."
        return _session.state()
