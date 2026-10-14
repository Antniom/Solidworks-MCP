"""
Tests for Server Phase 1 contracts and schemas
----------------------------------------------
Verifies tool catalog discovery, schema validity, and envelope generation.
"""

import unittest
from jsonschema import Draft202012Validator
from solidworks_mcp.server import _tool_definitions, _validate_tool_arguments


class TestServerPhase1(unittest.TestCase):
    """Tests for server tool definitions and contracts."""

    def test_tool_catalog_count_and_names(self):
        tools = _tool_definitions()
        # At least 26 tools (expanded in Phase 2 to 30)
        self.assertGreaterEqual(len(tools), 26)

        tool_names = {t.name for t in tools}
        expected_subset = {
            "connect_solidworks",
            "get_solidworks_info",
            "get_capabilities",
            "create_new_part",
            "create_new_assembly",
            "open_document",
            "save_document",
            "close_document",
            "create_sketch",
            "draw_rectangle",
            "draw_circle",
            "extrude_sketch",
            "cut_extrude",
            "fillet_edges",
            "chamfer_edges",
            "list_features",
            "get_sketch_status",
            "close_sketch",
            "set_units",
            "execute_python",
        }
        self.assertTrue(expected_subset.issubset(tool_names))

    def test_tool_schemas_validity(self):
        tools = _tool_definitions()
        for tool in tools:
            # Must be valid Draft 2020-12 schema
            Draft202012Validator.check_schema(tool.input_schema)

    def test_validate_tool_arguments(self):
        # Valid call to set_units
        err = _validate_tool_arguments("set_units", {"unit": "mm"})
        self.assertIsNone(err)

        # Invalid unit value
        err_bad_unit = _validate_tool_arguments("set_units", {"unit": "lightyears"})
        self.assertIsNotNone(err_bad_unit)

        # Unknown tool
        err_unknown = _validate_tool_arguments("non_existent_tool", {})
        self.assertIn("Unknown tool", err_unknown)


if __name__ == "__main__":
    unittest.main()
