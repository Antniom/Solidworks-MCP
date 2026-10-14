"""
Unit and Integration Tests for Phase 3: Assemblies and Drawings
---------------------------------------------------------------
Tests tool definitions, JSON schemas, argument validation, and offline error handling.
"""

import unittest
from jsonschema import Draft202012Validator

from solidworks_mcp.server import _tool_definitions, _validate_tool_arguments
from solidworks_mcp.automation.assemblies import AssemblyOperations
from solidworks_mcp.automation.drawings import DrawingOperations


class TestPhase3Tools(unittest.TestCase):
    """Tests for Phase 3 tool catalog, schemas, and argument validation."""

    def test_tool_catalog_includes_phase3(self):
        tools = _tool_definitions()
        # At least 37 tools (30 Phase 2 + 7 Phase 3, plus subsequent phases)
        self.assertGreaterEqual(len(tools), 37)

        tool_names = {t.name for t in tools}
        phase3_tools = {
            "add_component",
            "add_mate",
            "check_interference",
            "list_components",
            "create_new_drawing",
            "create_drawing_from_model",
            "export_drawing",
        }
        self.assertTrue(phase3_tools.issubset(tool_names), f"Missing tools: {phase3_tools - tool_names}")

    def test_phase3_schemas_validity(self):
        tools = _tool_definitions()
        for tool in tools:
            Draft202012Validator.check_schema(tool.input_schema)

    def test_add_component_validation(self):
        # Valid
        err = _validate_tool_arguments("add_component", {"component_path": "C:/models/part.sldprt", "x": 10.0, "unit": "mm"})
        self.assertIsNone(err)

        # Missing required component_path
        err_bad = _validate_tool_arguments("add_component", {"x": 10.0})
        self.assertIsNotNone(err_bad)
        self.assertIn("component_path", err_bad)

    def test_add_mate_validation(self):
        # Valid coincident mate
        err = _validate_tool_arguments("add_mate", {
            "mate_type": "coincident",
            "entity1": "Top Plane",
            "entity2": "Top Plane",
            "comp1": "cube-1",
            "comp2": "cube-2",
        })
        self.assertIsNone(err)

        # Invalid mate_type
        err_bad = _validate_tool_arguments("add_mate", {
            "mate_type": "nonexistent_mate",
            "entity1": "Top Plane",
            "entity2": "Top Plane",
        })
        self.assertIsNotNone(err_bad)

        # Missing required entity2
        err_missing = _validate_tool_arguments("add_mate", {
            "mate_type": "coincident",
            "entity1": "Top Plane",
        })
        self.assertIsNotNone(err_missing)

    def test_check_interference_validation(self):
        err = _validate_tool_arguments("check_interference", {"treat_coincidence_as_interference": True})
        self.assertIsNone(err)

    def test_export_drawing_validation(self):
        # Valid
        err = _validate_tool_arguments("export_drawing", {"filepath": "C:/drawings/sheet1.pdf", "format": "pdf"})
        self.assertIsNone(err)

        # Invalid format
        err_bad = _validate_tool_arguments("export_drawing", {"filepath": "C:/drawings/sheet1.xyz", "format": "xyz"})
        self.assertIsNotNone(err_bad)

        # Missing filepath
        err_missing = _validate_tool_arguments("export_drawing", {})
        self.assertIsNotNone(err_missing)

    def test_create_new_drawing_validation(self):
        # Valid
        err = _validate_tool_arguments("create_new_drawing", {"paper_size": "A3"})
        self.assertIsNone(err)

        # Invalid paper size
        err_bad = _validate_tool_arguments("create_new_drawing", {"paper_size": "A0"})
        self.assertIsNotNone(err_bad)


class TestAssemblyOperationsConstants(unittest.TestCase):
    """Test AssemblyOperations enum mappings and constants."""

    def test_mate_types_mapping(self):
        self.assertEqual(AssemblyOperations.MATE_TYPES["coincident"], 0)
        self.assertEqual(AssemblyOperations.MATE_TYPES["concentric"], 1)
        self.assertEqual(AssemblyOperations.MATE_TYPES["perpendicular"], 2)
        self.assertEqual(AssemblyOperations.MATE_TYPES["parallel"], 3)
        self.assertEqual(AssemblyOperations.MATE_TYPES["tangent"], 4)
        self.assertEqual(AssemblyOperations.MATE_TYPES["distance"], 5)
        self.assertEqual(AssemblyOperations.MATE_TYPES["angle"], 6)

    def test_drawing_export_formats(self):
        self.assertIn("pdf", DrawingOperations.SUPPORTED_EXPORT_FORMATS)
        self.assertIn("dxf", DrawingOperations.SUPPORTED_EXPORT_FORMATS)
        self.assertIn("dwg", DrawingOperations.SUPPORTED_EXPORT_FORMATS)
        self.assertIn("png", DrawingOperations.SUPPORTED_EXPORT_FORMATS)
