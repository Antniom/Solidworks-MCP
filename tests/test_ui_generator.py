"""
Unit tests for SolidWorks MCP Studio HTML Generator
--------------------------------------------------
Tests unit-aware bounds, angular dimensions, XSS escaping,
and Fast-Bridge port injection.
"""

from pathlib import Path
from solidworks_mcp.ui.generator import (
    compute_dimension_bounds,
    sanitize_for_script,
    generate_studio_html,
)


def test_linear_dimension_bounds():
    """Verify linear dimension bounds calculation for standard mm dimensions."""
    dim = {
        "full_name": "D1@Sketch1",
        "name": "D1",
        "value": 50.0,
        "units": "mm",
        "type": "Linear",
    }
    bounded = compute_dimension_bounds(dim)

    assert bounded["is_angle"] is False
    assert bounded["min"] == 10.0  # 50.0 * 0.2
    assert bounded["max"] == 125.0  # 50.0 * 2.5
    assert bounded["step"] == 0.5  # 50.0 * 0.01
    assert bounded["units"] == "mm"


def test_angular_dimension_bounds():
    """Verify angular dimension bounds (1-180 deg or 1-360 deg)."""
    dim_acute = {
        "full_name": "A1@Sketch1",
        "name": "A1",
        "value": 45.0,
        "units": "deg",
        "type": "Angular",
    }
    bounded_acute = compute_dimension_bounds(dim_acute)
    assert bounded_acute["is_angle"] is True
    assert bounded_acute["min"] == 1.0
    assert bounded_acute["max"] == 180.0
    assert bounded_acute["step"] == 1.0
    assert bounded_acute["units"] == "deg"

    dim_reflex = {
        "full_name": "A2@Sketch1",
        "name": "A2",
        "value": 270.0,
        "units": "deg",
        "type": "Angular",
    }
    bounded_reflex = compute_dimension_bounds(dim_reflex)
    assert bounded_reflex["max"] == 360.0


def test_zero_or_small_dimension_bounds():
    """Verify zero or tiny dimensions do not generate negative or zero ranges."""
    dim = {
        "full_name": "D0@Sketch1",
        "name": "D0",
        "value": 0.0,
        "units": "mm",
        "type": "Linear",
    }
    bounded = compute_dimension_bounds(dim)
    assert bounded["min"] > 0
    assert bounded["max"] >= 10.0
    assert bounded["step"] > 0


def test_readonly_dimension_flagging():
    """Verify driven or read-only dimensions are flagged readonly."""
    dim = {
        "full_name": "RD1@Sketch1",
        "name": "RD1",
        "value": 15.0,
        "units": "mm",
        "driven": True,
    }
    bounded = compute_dimension_bounds(dim)
    assert bounded.get("readonly") is True


def test_xss_script_sanitization():
    """Verify closing script tags are escaped to prevent XSS breakouts."""
    malicious_data = {
        "payload": "</script><script>alert('pwned')</script>",
        "comment": "<!-- safe -->",
    }
    sanitized = sanitize_for_script(malicious_data)
    assert "</script" not in sanitized
    assert "<\\/script" in sanitized
    assert "<!--" not in sanitized
    assert "<\\!--" in sanitized


def test_generate_studio_html_file(tmp_path: Path):
    """Verify complete HTML generation, port injection, and file write."""
    dims = [
        {"full_name": "D1@Sketch1", "name": "Width", "value": 100.0, "units": "mm", "owner_feature": "Sketch1"},
        {"full_name": "D1@Boss-Extrude1", "name": "Depth", "value": 25.0, "units": "mm", "owner_feature": "Boss-Extrude1"},
    ]
    out_file = tmp_path / "studio.html"

    html_content = generate_studio_html(
        model_title="Flange.SLDPRT",
        dimensions=dims,
        units="mm",
        initial_image_base64="data:image/png;base64,mock123",
        bbox={"dx": 100.0, "dy": 50.0, "dz": 25.0},
        bridge_port=54325,
        output_path=str(out_file),
    )

    assert out_file.exists()
    assert "Flange.SLDPRT" in html_content
    assert "54325" in html_content
    assert "window.__SW_STATE__" in html_content
    assert "data:image/png;base64,mock123" in html_content
    assert "Boss-Extrude1" in html_content


def test_semantic_hints_and_custom_labels(tmp_path: Path):
    """Verify semantic hint calculation and feature/dimension label injection."""
    from solidworks_mcp.ui.generator import infer_dimension_semantic_hint

    dim_extrude = {"name": "D1", "owner_feature": "Boss-Extrude1", "units": "mm"}
    assert "Extrusion Depth" in infer_dimension_semantic_hint(dim_extrude)

    dim_cut = {"name": "D1", "owner_feature": "Cut-Extrude2", "units": "mm"}
    assert "Cut Depth" in infer_dimension_semantic_hint(dim_cut)

    dim_fillet = {"name": "D1", "owner_feature": "Fillet1", "units": "mm"}
    assert "Fillet Radius" in infer_dimension_semantic_hint(dim_fillet)

    # Test custom labels in generated HTML
    out_file = tmp_path / "studio_labels.html"
    dims = [
        {"full_name": "D1@Boss-Extrude1@Part1.Part", "name": "D1", "value": 4.8, "units": "mm", "owner_feature": "Boss-Extrude1"},
        {"full_name": "D1@Boss-Extrude2@Part1.Part", "name": "D1", "value": 50.0, "units": "mm", "owner_feature": "Boss-Extrude2"},
    ]
    html = generate_studio_html(
        model_title="Bolt.SLDPRT",
        dimensions=dims,
        feature_labels={"Boss-Extrude1": "Hex Head", "Boss-Extrude2": "Shaft Body"},
        dimension_labels={"D1@Boss-Extrude1@Part1.Part": "Head Thickness", "D1@Boss-Extrude2@Part1.Part": "Shaft Length"},
        output_path=str(out_file),
    )
    assert "Hex Head" in html
    assert "Shaft Body" in html
    assert "Head Thickness" in html
    assert "Shaft Length" in html
