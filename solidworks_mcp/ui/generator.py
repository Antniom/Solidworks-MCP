"""
SolidWorks MCP - Studio HTML Generator
--------------------------------------
Generates unit-aware, self-contained interactive CAD studio widgets
with parametric dimension sliders, live Fast-Bridge port injection,
semantic feature/dimension labeling, XSS sanitization, and automatic artifact rendering.
"""

import os
import json
import math
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

from .bridge import get_bridge_port, set_current_app_html

logger = logging.getLogger("SolidWorksMCP.UIGenerator")

TEMPLATE_PATH = Path(__file__).parent / "template.html"


def infer_dimension_semantic_hint(dim: Dict[str, Any]) -> str:
    """
    Infer a human-readable engineering meaning for standard CAD dimensions (e.g. D1, D2).
    Helps users immediately understand what changing a dimension does.
    """
    full_name = str(dim.get("full_name", "")).lower()
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
        if name in ("d1", "depth"):
            return "Cut Depth"
        return "Cut Dimension"
    if "revolve" in owner:
        return "Revolve Angle"
    if "hole" in owner or "wizard" in owner:
        if "dia" in name or "d1" in name:
            return "Hole Diameter"
        return "Hole Depth"
    if "head" in owner:
        if name in ("d1", "depth", "thickness", "height"):
            return "Head Thickness"
        return "Head Dimension"
    if "shaft" in owner or "shank" in owner or "body" in owner or "rod" in owner or "pin" in owner:
        if name in ("d1", "length", "depth"):
            return "Shaft Length"
        return "Shaft Dimension"
    if "boss" in owner or "extrude" in owner or "extrusion" in owner:
        if name == "d1":
            return "Extrusion Depth / Length"
        return "Extrusion Dimension"
    if "sketch" in owner:
        if "dia" in name or "rad" in name:
            return "Diameter / Radius"
        if "d1" in name:
            return "Primary Sketch Dimension"
        if "d2" in name:
            return "Secondary Sketch Dimension"
        return "Sketch Dimension"
    if name == "d1":
        return "Primary Dimension / Length"
    return "Dimension"


def compute_standard_bounds(val: float, is_angle: bool = False) -> Dict[str, float]:
    """
    Compute deterministic engineering bracket scales for parametric CAD sliders.
    Both linear and angular dimensions are mapped to clean decade brackets so that
    sliders never show arbitrary floating-point boundaries and remain perfectly
    consistent across all connected studio views (in-chat widget and browser tab).
    """
    if is_angle:
        return {
            "min": 1.0,
            "max": 360.0 if val > 180.0 else 180.0,
            "step": 1.0,
        }
    v = max(0.1, abs(val))
    if v <= 15.0:
        return {"min": 0.5, "max": 30.0, "step": 0.5}
    elif v <= 30.0:
        return {"min": 1.0, "max": 60.0, "step": 0.5}
    elif v <= 60.0:
        return {"min": 2.0, "max": 120.0, "step": 1.0}
    elif v <= 120.0:
        return {"min": 5.0, "max": 250.0, "step": 1.0}
    elif v <= 250.0:
        return {"min": 10.0, "max": 500.0, "step": 2.0}
    elif v <= 500.0:
        return {"min": 20.0, "max": 1000.0, "step": 5.0}
    else:
        max_v = float(math.ceil(v * 2.0 / 50.0) * 50.0)
        return {"min": 50.0, "max": max_v, "step": 5.0}


def compute_dimension_bounds(dim: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compute unit-aware minimum, maximum, and step bounds for dimension sliders.
    Separates angular dimensions (deg/rad) from linear dimensions (mm/in) using
    standard CAD engineering brackets.
    """
    if not isinstance(dim, dict):
        return {}

    val = float(dim.get("value", 10.0))
    units = str(dim.get("units", "mm")).lower()
    dim_type = str(dim.get("type", "")).lower()

    is_angle = "deg" in units or "rad" in units or "angular" in dim_type or dim.get("type_code") in (1, 3, 16)

    dim_copy = dict(dim)
    bounds = compute_standard_bounds(val, is_angle)

    if is_angle:
        dim_copy["units"] = "deg"
        dim_copy["is_angle"] = True
    else:
        dim_copy["is_angle"] = False

    dim_copy["min"] = bounds["min"]
    dim_copy["max"] = bounds["max"]
    dim_copy["step"] = bounds["step"]

    # Attach inferred semantic hint
    dim_copy["semantic_hint"] = infer_dimension_semantic_hint(dim_copy)

    # Flag driven or read-only dimensions if specified
    if dim.get("driven") or dim.get("read_only"):
        dim_copy["readonly"] = True

    return dim_copy


def sanitize_for_script(data: Any) -> str:
    """
    Serialize data to JSON while safely escaping closing script tags
    to prevent XSS and HTML parser breakouts.
    """
    raw_json = json.dumps(data)
    # Prevent </script> tags from breaking script block in HTML
    safe_json = raw_json.replace("</script", "<\\/script").replace("<!--", "<\\!--")
    return safe_json


def generate_studio_html(
    model_title: str,
    dimensions: List[Dict[str, Any]],
    units: str = "mm",
    initial_image_base64: str = "",
    bbox: Optional[Dict[str, Any]] = None,
    bridge_port: Optional[int] = None,
    feature_labels: Optional[Dict[str, str]] = None,
    dimension_labels: Optional[Dict[str, str]] = None,
    output_path: Optional[str] = None,
) -> str:
    """
    Generate the complete interactive SolidWorks Studio HTML document.

    Args:
        model_title: Name of the active part/assembly (e.g. 'bracket.sldprt')
        dimensions: List of dimension dicts from get_dimensions()
        units: Active length unit (default: 'mm')
        initial_image_base64: Data-URI of initial viewport snapshot
        bbox: Bounding box dictionary from inspect_model()
        bridge_port: Port of local Fast-Bridge HTTP daemon (defaults to active bridge port)
        feature_labels: Optional mapping of feature names to friendly labels (e.g. {'Boss-Extrude1': 'Bolt Head'})
        dimension_labels: Optional mapping of dimension names to friendly labels (e.g. {'D1@Boss-Extrude1@Part1.Part': 'Head Height'})
        output_path: Filepath where the generated HTML file should be saved

    Returns:
        Generated HTML string
    """
    # Load HTML template
    if not TEMPLATE_PATH.exists():
        raise FileNotFoundError(f"Studio HTML template not found at: {TEMPLATE_PATH}")

    template_str = TEMPLATE_PATH.read_text(encoding="utf-8")

    # Resolve bridge port
    resolved_port = bridge_port or get_bridge_port() or 54321

    # Extract dimensions list if a dict wrapper was provided
    if isinstance(dimensions, dict):
        dimensions = dimensions.get("dimensions", [])
    elif not isinstance(dimensions, list):
        dimensions = []

    # Process and compute bounds for all dimensions
    processed_dims = [compute_dimension_bounds(d) for d in dimensions if isinstance(d, dict) and d]

    # Clean display title and units
    clean_title = str(model_title or "SolidWorks Part")
    if "mock" in clean_title.lower():
        clean_title = "SolidWorks Part"

    clean_units = str(units or "mm")
    if "mock" in clean_units.lower() or not clean_units.strip():
        clean_units = "mm"

    # Build injected state object
    state_payload = {
        "bridgePort": resolved_port,
        "modelTitle": clean_title,
        "units": clean_units,
        "initialImage": str(initial_image_base64 or ""),
        "bbox": bbox,
        "featureLabels": feature_labels or {},
        "dimensionLabels": dimension_labels or {},
        "dimensions": processed_dims,
    }

    state_json = sanitize_for_script(state_payload)
    injected_script = f"window.__SW_STATE__ = {state_json};"

    # Replace window.__SW_STATE__ initialization in template
    target_needle = "const state = window.__SW_STATE__ || {"
    replacement_code = f"{injected_script}\n    const state = window.__SW_STATE__ || {{"

    if target_needle in template_str:
        final_html = template_str.replace(target_needle, replacement_code, 1)
    else:
        # Fallback injection right before </head>
        final_html = template_str.replace("</head>", f"<script>{injected_script}</script></head>", 1)

    # Update bridge server's /api/app.html cache
    try:
        set_current_app_html(final_html)
    except Exception:
        pass

    # Save to disk if requested
    if output_path:
        out_file = Path(output_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(final_html, encoding="utf-8")
        logger.info(f"Interactive CAD Studio HTML written to: {out_file}")

    return final_html
