"""The studio surface must be valid A2UI 1.0 (basic catalog)."""

import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from solidworks_mcp.ui import a2ui

SCHEMAS = Path(__file__).parent / "fixtures" / "a2ui_v1_0"

RAW_DIMS = [
    {"full_name": "D1@Boss-Extrude1@Part1.Part", "name": "D1", "owner_feature": "Boss-Extrude1", "value": 4.8, "units": "mm"},
    {"full_name": "D1@Boss-Extrude2@Part1.Part", "name": "D1", "owner_feature": "Boss-Extrude2", "value": 50.0, "units": "mm"},
    {"full_name": "D2@Sketch3@Part1.Part", "name": "D2", "owner_feature": "Sketch3", "value": 45.0, "units": "deg"},
    {"full_name": "D9@Sketch3@Part1.Part", "name": "D9", "owner_feature": "Sketch3", "value": 7.0, "units": "mm", "driven": True},
]


def _validator(schema_name: str = "agent_to_renderer.json") -> Draft202012Validator:
    def load(name):
        return json.loads((SCHEMAS / name).read_text(encoding="utf-8"))

    base = "https://a2ui.org/specification/v1_0/"
    common = Resource.from_contents(load("common_types.json"))
    catalog = Resource.from_contents(load("basic_catalog.json"))
    # The schemas refer to each other by sibling file name ("common_types.json", "catalog.json").
    registry = Registry().with_resources([
        (base + "common_types.json", common),
        (base + "catalog.json", catalog),
        (base + "catalogs/basic/common_types.json", common),
        (base + "catalogs/basic/catalog.json", catalog),
    ])
    return Draft202012Validator(load(schema_name), registry=registry)


def _dims():
    return a2ui.build_dimension_model(
        RAW_DIMS,
        feature_labels={"Boss-Extrude1": "Hex Bolt Head"},
        dimension_labels={"D1@Boss-Extrude1@Part1.Part": "Head Thickness"},
    )


def test_surface_messages_are_valid_a2ui():
    validator = _validator()
    messages = a2ui.build_surface_messages("Bolt.SLDPRT", _dims(), "data:image/png;base64,AAAA", {"dx": 1, "dy": 2, "dz": 3})
    assert len(messages) == 1 and messages[0]["version"] == "1.0"
    surface = messages[0]["createSurface"]
    assert surface["catalogId"] == "https://a2ui.org/specification/v1_0/catalogs/basic/catalog.json"
    assert surface["dataModel"]["dims"]["d0"]["value"] == 4.8
    for message in messages + [a2ui.data_update("/status", "ok")]:
        errors = [e.message for e in validator.iter_errors(message)]
        assert errors == [], errors


def test_empty_model_is_still_valid_a2ui():
    validator = _validator()
    for message in a2ui.build_surface_messages("Empty", {}):
        assert list(validator.iter_errors(message)) == []


def test_schema_rejects_the_old_binding_syntax():
    """Guards the validator itself: a v0.9-style {"path": ...} binding must not pass as 1.0."""
    message = a2ui.build_surface_messages("Bolt", _dims())[0]
    message["createSurface"]["components"][2]["text"] = {"path": "/model/title"}
    assert list(_validator().iter_errors(message)) != []


def test_component_ids_are_identifiers():
    # A2UI 1.0 renderers reject ids such as "view-card".
    for component in a2ui.build_components(_dims()):
        assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", component["id"]), component["id"]


def test_component_tree_is_closed_and_rooted():
    components = a2ui.build_components(_dims())
    ids = [c["id"] for c in components]
    assert len(ids) == len(set(ids)), "duplicate component ids"
    assert "root" in ids
    referenced = set()
    for c in components:
        referenced.update(c.get("children", []))
        if "child" in c:
            referenced.add(c["child"])
    assert referenced <= set(ids), referenced - set(ids)


def test_dimension_model_labels_groups_and_skips_driven():
    dims = _dims()
    assert len(dims) == 3  # the driven dimension is not adjustable
    head = dims["d0"]
    assert head["label"] == "Head Thickness" and head["feature"] == "Hex Bolt Head"
    assert head["min"] <= head["value"] <= head["max"]
    angle = dims["d2"]
    assert angle["units"] == "deg" and angle["min"] == 0 and angle["max"] == 180 and angle["steps"] == 180
    assert head["steps"] == 59  # 0.5 mm steps between 0.5 and 30


def test_linear_sliders_never_reach_zero():
    # SolidWorks rejects non-positive linear dimensions.
    for value in (0.4, 1.0, 12.0, 300.0, 5000.0):
        bounds = a2ui.compute_slider_bounds(value)
        assert 0 < bounds["min"] <= value <= bounds["max"]


def test_change_summary_lists_only_changed_dimensions():
    dims = _dims()
    assert a2ui.build_change_summary(dims) == ""
    dims["d1"]["value"] = 65.0
    summary = a2ui.build_change_summary(dims)
    assert "D1@Boss-Extrude2@Part1.Part" in summary and "from 50 to 65 mm" in summary
    assert "Head Thickness" not in summary
