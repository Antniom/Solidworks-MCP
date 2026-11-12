"""
SolidWorks Interactive Studio - A2UI surface builder
----------------------------------------------------
Describes the studio as A2UI 1.0 messages (https://github.com/a2ui-project/a2ui)
using only the basic catalog, so any A2UI renderer can draw it. The bundled
host page (ui/host/studio.js) renders these with the stock A2UI web renderer.

Data model layout:
    /model     {title, info}
    /viewport  {image: data-URI, view: [view name]}
    /dims      {d0: {name, label, feature, value, original, units, min, max, steps}, ...}
    /status    one-line status text
    /summary   ready-made prompt describing what the user changed
"""

import math
from typing import Any, Dict, List, Optional

A2UI_VERSION = "1.0"
SURFACE_ID = "solidworks-studio"
BASIC_CATALOG_ID = "https://a2ui.org/specification/v1_0/catalogs/basic/catalog.json"

VIEW_NAMES = ["*Isometric", "*Front", "*Top", "*Right", "*Back", "*Left", "*Bottom"]

Message = Dict[str, Any]


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
        # A2UI Slider takes a number of divisions rather than a step size.
        "steps": max(1, round((bounds["max"] - bounds["min"]) / bounds["step"])),
    }


def build_dimension_model(
    dimensions: Any,
    feature_labels: Optional[Dict[str, str]] = None,
    dimension_labels: Optional[Dict[str, str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Convert get_dimensions() output into the keyed /dims data-model object."""
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
# A2UI messages
# ============================================================================

def data_update(path: str, value: Any) -> Message:
    """A2UI updateDataModel message for the studio surface."""
    return {
        "version": A2UI_VERSION,
        "updateDataModel": {"surfaceId": SURFACE_ID, "path": path, "value": value},
    }


# A2UI 1.0 component ids must be identifiers (letters, digits, underscores).
def _text(component_id: str, text: Any, variant: str = "body") -> Dict[str, Any]:
    return {"id": component_id, "component": "Text", "text": text, "variant": variant}


def _button(component_id: str, label: str, action: str, context: Dict[str, Any], variant: str = "default") -> List[Dict[str, Any]]:
    return [
        {
            "id": component_id,
            "component": "Button",
            "child": f"{component_id}_label",
            "variant": variant,
            "action": {"event": {"name": action, "context": context}},
        },
        _text(f"{component_id}_label", label),
    ]


def build_components(dims: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Component tree for the studio: viewport card on the left, sliders grouped by feature on the right."""
    components: List[Dict[str, Any]] = [
        {"id": "root", "component": "Column", "children": ["header", "body", "footer"]},
        {"id": "header", "component": "Row", "children": ["title", "info"], "justify": "spaceBetween", "align": "center"},
        _text("title", {"@path": "/model/title"}),
        _text("info", {"@path": "/model/info"}, "caption"),
        {"id": "body", "component": "Row", "children": ["view_card", "controls"], "align": "start"},
        {"id": "view_card", "component": "Card", "child": "view_col", "weight": 3},
        {"id": "view_col", "component": "Column", "children": ["viewport", "view_picker", "refresh"]},
        {
            "id": "viewport",
            "component": "Image",
            "url": {"@path": "/viewport/image"},
            "description": "SolidWorks viewport",
            "fit": "contain",
            "variant": "largeFeature",
        },
        {
            "id": "view_picker",
            "component": "ChoicePicker",
            "label": "View",
            "variant": "mutuallyExclusive",
            "displayStyle": "chips",
            "options": [{"label": name.lstrip("*"), "value": name} for name in VIEW_NAMES],
            "value": {"@path": "/viewport/view"},
        },
        *_button("refresh", "Refresh view", "capture", {"view": {"@path": "/viewport/view"}}),
    ]

    groups: Dict[str, List[str]] = {}
    for key, dim in dims.items():
        groups.setdefault(dim["feature"], []).append(key)

    control_ids: List[str] = []
    if not groups:
        control_ids.append("no_dims")
        components.append(_text("no_dims", "No driving dimensions were found in this model."))
    for index, (feature, keys) in enumerate(groups.items()):
        group_id = f"group_{index}"
        control_ids.append(group_id)
        children = [f"{group_id}_title"]
        components.append({"id": group_id, "component": "Card", "child": f"{group_id}_col"})
        components.append(_text(f"{group_id}_title", feature))
        for key in keys:
            dim = dims[key]
            children += [f"slider_{key}", f"value_{key}"]
            components.append({
                "id": f"slider_{key}",
                "component": "Slider",
                "label": f"{dim['label']} ({dim['units']})",
                "min": dim["min"],
                "max": dim["max"],
                "steps": dim["steps"],
                "value": {"@path": f"/dims/{key}/value"},
            })
            components.append(_text(f"value_{key}", dim["name"], "caption"))
        components.append({"id": f"{group_id}_col", "component": "Column", "children": children})
    components.append({"id": "controls", "component": "Column", "children": control_ids, "weight": 2})

    components += [
        {"id": "footer", "component": "Row", "children": ["status", "reset", "prompt"], "justify": "spaceBetween", "align": "center"},
        _text("status", {"@path": "/status"}, "caption"),
        *_button("reset", "Reset", "reset", {}),
        *_button("prompt", "Prompt Agent", "prompt_agent", {"summary": {"@path": "/summary"}}, "primary"),
    ]
    return components


def build_surface_messages(
    model_title: str,
    dims: Dict[str, Dict[str, Any]],
    image_data_uri: str = "",
    bbox: Optional[Dict[str, Any]] = None,
    view_name: str = "*Isometric",
    status: str = "Drag a slider to rebuild the model in SolidWorks.",
) -> List[Message]:
    """The A2UI message that creates and populates the studio surface."""
    return [{
        "version": A2UI_VERSION,
        "createSurface": {
            "surfaceId": SURFACE_ID,
            "catalogId": BASIC_CATALOG_ID,
            "components": build_components(dims),
            "dataModel": {
                "model": {"title": model_title or "SolidWorks Model", "info": describe_model(bbox, len(dims))},
                "viewport": {"image": image_data_uri or "", "view": [view_name]},
                "dims": dims,
                "status": status,
                "summary": build_change_summary(dims),
            },
        },
    }]
