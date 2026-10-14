"""
Unit and Integration Tests for Phase 2: Verified Part Modelling
---------------------------------------------------------------
Tests declarative selection targets, model verification engine,
tool schema validity, and tool discovery for Phase 2.
"""

import unittest
from jsonschema import Draft202012Validator

from solidworks_mcp.core.selection import (
    SelectionTarget,
    SelectionResult,
    DeclarativeSelectionResolver,
)
from solidworks_mcp.core.verification import ModelVerifier, VerificationReport, DimensionCheck
from solidworks_mcp.server import _tool_definitions, _validate_tool_arguments


class TestDeclarativeSelection(unittest.TestCase):
    """Tests for declarative selection targets and resolution logic."""

    def test_plane_mapping(self):
        self.assertEqual(DeclarativeSelectionResolver.PLANE_MAP["front"], "Front Plane")
        self.assertEqual(DeclarativeSelectionResolver.PLANE_MAP["top"], "Top Plane")
        self.assertEqual(DeclarativeSelectionResolver.PLANE_MAP["right"], "Right Plane")

    def test_direction_normals(self):
        self.assertEqual(DeclarativeSelectionResolver.DIRECTION_NORMALS["top"], (0.0, 1.0, 0.0))
        self.assertEqual(DeclarativeSelectionResolver.DIRECTION_NORMALS["bottom"], (0.0, -1.0, 0.0))
        self.assertEqual(DeclarativeSelectionResolver.DIRECTION_NORMALS["front"], (0.0, 0.0, 1.0))
        self.assertEqual(DeclarativeSelectionResolver.DIRECTION_NORMALS["back"], (0.0, 0.0, -1.0))

    def test_selection_target_defaults(self):
        target = SelectionTarget(target_type="face", point=[0.0, 50.0, 20.0], unit="mm")
        self.assertEqual(target.target_type, "face")
        self.assertEqual(target.point, [0.0, 50.0, 20.0])
        self.assertEqual(target.unit, "mm")
        self.assertFalse(target.append)


class TestModelVerifier(unittest.TestCase):
    """Tests for dimensional checks and verification report generation."""

    def test_offline_verification_failure(self):
        report = ModelVerifier.verify(None)
        self.assertFalse(report.passed)
        self.assertFalse(report.rebuild_clean)
        self.assertEqual(report.body_count, 0)
        self.assertIn("No active document available", report.warnings[0])

    def test_dimension_check_matching(self):
        dim_check = DimensionCheck(
            expected_mm={"dx": 100.0, "dy": 50.0, "dz": 20.0},
            actual_mm={"dx": 100.05, "dy": 49.98, "dz": 20.02},
            diffs_mm={"dx": 0.05, "dy": 0.02, "dz": 0.02},
            tolerance_mm=0.1,
            passed=True,
        )
        self.assertTrue(dim_check.passed)
        self.assertLess(dim_check.diffs_mm["dx"], dim_check.tolerance_mm)

    def test_dimension_check_tolerance_exceeded(self):
        dim_check = DimensionCheck(
            expected_mm={"dx": 100.0, "dy": 50.0, "dz": 20.0},
            actual_mm={"dx": 101.5, "dy": 50.0, "dz": 20.0},
            diffs_mm={"dx": 1.5, "dy": 0.0, "dz": 0.0},
            tolerance_mm=0.2,
            passed=False,
        )
        self.assertFalse(dim_check.passed)
        self.assertGreater(dim_check.diffs_mm["dx"], dim_check.tolerance_mm)


class TestPhase2Tools(unittest.TestCase):
    """Tests for Phase 2 tool definitions, schemas, and argument validation."""

    def test_tool_catalog_includes_phase2(self):
        tools = _tool_definitions()
        # Catalog has at least 30 tools (including Phase 2 tools)
        self.assertGreaterEqual(len(tools), 30)

        tool_names = {t.name for t in tools}
        phase2_tools = {"inspect_model", "verify_model", "select_entity", "capture_viewport"}
        self.assertTrue(phase2_tools.issubset(tool_names))

    def test_phase2_schemas_validity(self):
        tools = _tool_definitions()
        for tool in tools:
            Draft202012Validator.check_schema(tool.input_schema)

    def test_phase2_argument_validation(self):
        # Valid select_entity
        err = _validate_tool_arguments("select_entity", {"target_type": "face", "direction": "top"})
        self.assertIsNone(err)

        # Invalid target_type
        err_bad = _validate_tool_arguments("select_entity", {"target_type": "invalid_entity_type"})
        self.assertIsNotNone(err_bad)

        # Valid verify_model
        err_ver = _validate_tool_arguments("verify_model", {"expected_dx": 100, "tolerance_mm": 0.1})
        self.assertIsNone(err_ver)


if __name__ == "__main__":
    unittest.main()
