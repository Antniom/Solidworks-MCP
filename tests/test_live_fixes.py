"""
Pure-function tests for fixes found by live testing against SolidWorks 2025
(no SolidWorks needed): CS1061 cast repair, macro wrapping, transforms,
topology unit conversion and unit handling after a module reload.
"""

import importlib

from solidworks_mcp.core.com_compat import transform_point
from solidworks_mcp.core.mecagent_runner import MecAgentRunnerBridge, cast_repair


def _cs1061(line, col, src, member):
    return {"id": "CS1061", "line": line, "column": col,
            "message": f"'{src}' does not contain a definition for '{member}' and no accessible extension method"}


def test_cast_repair_document_member_uses_doc_type():
    code = 'IModelDoc2 doc = null;\nvar f = doc.FeatureByName("A");'
    fixed, reps = cast_repair(code, [_cs1061(2, 13, "IModelDoc2", "FeatureByName")], doc_type_hint="Assembly")
    assert fixed.splitlines()[1] == 'var f = ((IAssemblyDoc)doc).FeatureByName("A");'
    assert reps[0]["to"] == "IAssemblyDoc"


def test_cast_repair_handles_nested_cast_receiver():
    code = "var b = ((IModelDoc2)swApp.ActiveDoc).GetBodies2(0, true);"
    fixed, _ = cast_repair(code, [_cs1061(1, 39, "IModelDoc2", "GetBodies2")], doc_type_hint="Part")
    assert fixed == "var b = ((IPartDoc)((IModelDoc2)swApp.ActiveDoc)).GetBodies2(0, true);"


def test_cast_repair_untyped_receiver_uses_variable_name():
    code = "var c = comps[0].GetBodies2(0);"
    fixed, _ = cast_repair(code, [_cs1061(1, 18, "object", "GetBodies2")])
    assert fixed == "var c = ((IComponent2)comps[0]).GetBodies2(0);"


def test_cast_repair_ignores_unknown_members_and_other_errors():
    code = "var x = doc.NotARealMember();"
    fixed, reps = cast_repair(code, [_cs1061(1, 13, "IModelDoc2", "NotARealMember"),
                                     {"id": "CS0103", "line": 1, "column": 1, "message": "x"}])
    assert fixed == code and reps == []


def test_macro_wrapped_as_cell_with_bound_app():
    src = "public class DynamicClass { public static object Execute(SldWorks swApp, object[] args) { return 1; } }"
    cell = MecAgentRunnerBridge._macro_to_cell(src, [1, "a", 2.5])
    assert cell.splitlines()[-1] == 'return DynamicClass.Execute((SldWorks)swApp, new object[] { 1, "a", 2.5d });'


def test_transform_point_row_vector_convention():
    # 90° about Z (row vectors: x-axis image = (0,1,0)) then +10 mm in X.
    arr = [0, 1, 0, -1, 0, 0, 0, 0, 1, 0.01, 0, 0, 1, 0, 0, 0]
    assert transform_point(arr, [1, 0, 0]) == [0.01, 1.0, 0.0]


def test_topology_units_converted_to_mm():
    from solidworks_mcp.server import _fix_topology_units
    out = _fix_topology_units("Area: 0.004000 mm²\n    Length: 0.080000 mm\nCurrent Value: 10 mm")
    assert out.splitlines() == ["Area: 4000.000 mm²", "    Length: 80.000 mm", "Current Value: 10 mm"]


def test_units_survive_module_reload():
    from solidworks_mcp.utils import units
    conv = units.UnitConverter("mm")
    importlib.reload(units)
    assert conv.to_meters(5) == 0.005 and conv.from_meters(0.005) == 5.0
