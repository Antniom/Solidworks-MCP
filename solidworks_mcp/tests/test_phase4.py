"""
Unit and Integration Tests for Phase 4: Simulation and Verification
-------------------------------------------------------------------
Tests tool definitions, JSON schemas, argument validation, and offline error handling
for FEA Simulation capabilities.
"""

import unittest
from jsonschema import Draft202012Validator

from solidworks_mcp.server import _tool_definitions, _validate_tool_arguments
from solidworks_mcp.automation.simulation import SimulationOperations


class TestPhase4SimulationTools(unittest.TestCase):
    """Tests for Phase 4 simulation tool catalog, schemas, and argument validation."""

    def test_tool_catalog_includes_phase4(self):
        tools = _tool_definitions()
        # At least 43 tools (including subsequent phase additions)
        self.assertGreaterEqual(len(tools), 43)

        tool_names = {t.name for t in tools}
        phase4_tools = {
            "create_simulation_study",
            "set_study_material",
            "add_study_fixture",
            "add_study_load",
            "mesh_and_run_simulation",
            "get_simulation_results",
        }
        self.assertTrue(phase4_tools.issubset(tool_names), f"Missing tools: {phase4_tools - tool_names}")

    def test_phase4_schemas_validity(self):
        tools = _tool_definitions()
        for tool in tools:
            Draft202012Validator.check_schema(tool.input_schema)

    def test_create_simulation_study_validation(self):
        # Valid call
        err = _validate_tool_arguments("create_simulation_study", {"study_name": "BeamTest", "study_type": "static"})
        self.assertIsNone(err)

        # Valid with default
        err = _validate_tool_arguments("create_simulation_study", {})
        self.assertIsNone(err)

        # Invalid study type
        err = _validate_tool_arguments("create_simulation_study", {"study_type": "invalid_type"})
        self.assertIsNotNone(err)

    def test_set_study_material_validation(self):
        # Valid call
        err = _validate_tool_arguments("set_study_material", {"material_name": "AISI 1020"})
        self.assertIsNone(err)

        # Missing required material_name
        err = _validate_tool_arguments("set_study_material", {})
        self.assertIsNotNone(err)

    def test_add_study_fixture_validation(self):
        # Valid calls
        err = _validate_tool_arguments("add_study_fixture", {"fixture_type": "fixed", "direction": "left"})
        self.assertIsNone(err)

        err = _validate_tool_arguments("add_study_fixture", {"fixture_type": "immovable", "face_point": [0.0, 10.0, 5.0]})
        self.assertIsNone(err)

        # Invalid fixture type
        err = _validate_tool_arguments("add_study_fixture", {"fixture_type": "invalid_fixture"})
        self.assertIsNotNone(err)

        # Invalid direction
        err = _validate_tool_arguments("add_study_fixture", {"direction": "sideways"})
        self.assertIsNotNone(err)

    def test_add_study_load_validation(self):
        # Valid pressure call
        err = _validate_tool_arguments("add_study_load", {"load_type": "pressure", "value": 50000.0, "unit": "N/m^2", "direction": "top"})
        self.assertIsNone(err)

        # Valid force call
        err = _validate_tool_arguments("add_study_load", {"load_type": "force", "value": 500.0, "unit": "N", "direction": "bottom"})
        self.assertIsNone(err)

        # Missing required value
        err = _validate_tool_arguments("add_study_load", {"load_type": "pressure"})
        self.assertIsNotNone(err)

        # Invalid load type
        err = _validate_tool_arguments("add_study_load", {"load_type": "torque", "value": 100.0})
        self.assertIsNotNone(err)

    def test_mesh_and_run_simulation_validation(self):
        err = _validate_tool_arguments("mesh_and_run_simulation", {"element_size_mm": 3.0, "tolerance_mm": 0.15})
        self.assertIsNone(err)

        err = _validate_tool_arguments("mesh_and_run_simulation", {})
        self.assertIsNone(err)

    def test_get_simulation_results_validation(self):
        err = _validate_tool_arguments("get_simulation_results", {"study_name": "BeamFEA"})
        self.assertIsNone(err)

        err = _validate_tool_arguments("get_simulation_results", {})
        self.assertIsNone(err)


class DummySimulationApp(SimulationOperations):
    """Stub harness to verify offline error behaviors."""
    def __init__(self):
        self.is_connected = False
        self.app = None

    def _result(self, success, message, error_code=0, data=None):
        return {"success": success, "message": message, "error_code": int(error_code), "data": data}


class TestSimulationOfflineHandling(unittest.TestCase):
    """Verify clean offline failures when disconnected."""

    def setUp(self):
        self.sim = DummySimulationApp()

    def test_create_study_offline(self):
        res = self.sim.create_simulation_study("Test")
        self.assertFalse(res["success"])

    def test_set_material_offline(self):
        res = self.sim.set_study_material("AISI 1020")
        self.assertFalse(res["success"])

    def test_add_fixture_offline(self):
        res = self.sim.add_study_fixture("fixed", direction="left")
        self.assertFalse(res["success"])

    def test_add_load_offline(self):
        res = self.sim.add_study_load("pressure", value=1000.0, direction="top")
        self.assertFalse(res["success"])

    def test_mesh_and_run_offline(self):
        res = self.sim.mesh_and_run_simulation()
        self.assertFalse(res["success"])

    def test_get_results_offline(self):
        res = self.sim.get_simulation_results()
        self.assertFalse(res["success"])


if __name__ == "__main__":
    unittest.main()
