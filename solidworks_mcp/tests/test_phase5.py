"""
Unit and Integration Tests for Phase 5: Knowledge and Operational Hardening
----------------------------------------------------------------------------
Tests KnowledgeManager, CheckpointManager, tool schemas, and argument validation.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from jsonschema import Draft202012Validator

from solidworks_mcp.server import _tool_definitions, _validate_tool_arguments
from solidworks_mcp.core.knowledge import (
    KnowledgeManager,
    KnowledgeRecord,
    PromotionState,
    get_knowledge_manager,
)
from solidworks_mcp.core.checkpoint import (
    CheckpointManager,
    CheckpointMetadata,
    get_checkpoint_manager,
)


class TestPhase5ToolsAndSchemas(unittest.TestCase):
    """Tests for Phase 5 tool definitions and schema validity."""

    def test_tool_catalog_includes_phase5(self):
        tools = _tool_definitions()
        # 43 from Phase 4 + 4 Phase 5 tools + 4 Diagnostics/Recovery tools = 51 total tools
        self.assertEqual(len(tools), 51)

        tool_names = {t.name for t in tools}
        phase5_tools = {
            "search_knowledge",
            "record_knowledge",
            "create_checkpoint",
            "rollback_checkpoint",
        }
        self.assertTrue(phase5_tools.issubset(tool_names), f"Missing tools: {phase5_tools - tool_names}")

    def test_all_schemas_validity(self):
        tools = _tool_definitions()
        for tool in tools:
            Draft202012Validator.check_schema(tool.input_schema)

    def test_search_knowledge_validation(self):
        err = _validate_tool_arguments("search_knowledge", {"query": "simulation", "min_confidence": 0.5})
        self.assertIsNone(err)

        err = _validate_tool_arguments("search_knowledge", {})
        self.assertIsNone(err)

    def test_record_knowledge_validation(self):
        # Valid call
        valid_args = {
            "id": "sw_test_quirk",
            "title": "Test Quirk",
            "problem": "Test problem description",
            "successful_approach": "Test approach",
            "promotion_state": "discovered",
        }
        err = _validate_tool_arguments("record_knowledge", valid_args)
        self.assertIsNone(err)

        # Missing required problem
        invalid_args = {
            "id": "sw_test_quirk",
            "title": "Test Quirk",
            "successful_approach": "Test approach",
        }
        err = _validate_tool_arguments("record_knowledge", invalid_args)
        self.assertIsNotNone(err)

    def test_create_checkpoint_validation(self):
        err = _validate_tool_arguments("create_checkpoint", {"name": "before_cut"})
        self.assertIsNone(err)

        # Missing name
        err = _validate_tool_arguments("create_checkpoint", {})
        self.assertIsNotNone(err)

    def test_rollback_checkpoint_validation(self):
        err = _validate_tool_arguments("rollback_checkpoint", {"name": "before_cut"})
        self.assertIsNone(err)

        # Missing name
        err = _validate_tool_arguments("rollback_checkpoint", {})
        self.assertIsNotNone(err)


class TestKnowledgeManager(unittest.TestCase):
    """Tests for KnowledgeManager storage, search, and promotion."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.km = KnowledgeManager(base_dir=Path(self.temp_dir))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_seed_and_search(self):
        seeded = self.km.seed_default_knowledge()
        self.assertGreaterEqual(seeded, 9)

        # Search by tag
        sim_recs = self.km.search(tags=["simulation"])
        self.assertGreaterEqual(len(sim_recs), 5)

        # Search by interface
        face_recs = self.km.search(interface="IFace2")
        self.assertGreaterEqual(len(face_recs), 1)

        # Search by query keyword
        results = self.km.search(query="RunAnalysis")
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0].api_member, "RunAnalysis")

    def test_record_and_promote(self):
        rec = KnowledgeRecord(
            id="test_new_quirk",
            title="Newly Observed Quirk",
            date="2026-10-01",
            product_version="33.5.0",
            api_interface="ITestInterface",
            api_member="TestMethod",
            problem="Issue description",
            attempted_approaches=["Approach 1"],
            successful_approach="Working solution",
            source="test_harness",
            confidence=0.5,
            verification_method="unit_test",
            promotion_state=PromotionState.DISCOVERED,
            tags=["test", "draft"],
        )
        self.km.save_record(rec)

        loaded = self.km.get_record("test_new_quirk")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.promotion_state, PromotionState.DISCOVERED)

        # Promote to experimentally verified
        promoted = self.km.promote_record(
            "test_new_quirk",
            PromotionState.EXPERIMENTALLY_VERIFIED,
            evidence_note="Verified in test harness",
            new_confidence=0.9,
        )
        self.assertTrue(promoted)

        loaded_promoted = self.km.get_record("test_new_quirk")
        self.assertEqual(loaded_promoted.promotion_state, PromotionState.EXPERIMENTALLY_VERIFIED)
        self.assertEqual(loaded_promoted.confidence, 0.9)
        self.assertIn("Verified in test harness", loaded_promoted.evidence_notes)


class DummyFeature:
    def __init__(self, name, next_feat=None):
        self.Name = name
        self.GetNextFeature = next_feat


class DummyModelDoc:
    def __init__(self, title, features):
        self.GetTitle = title
        self.FirstFeature = features[0] if features else None
        self.deleted_features = []

    def SaveCopyAs(self, path):
        return 0

    def ForceRebuild3(self, flag):
        return True


class TestCheckpointManager(unittest.TestCase):
    """Tests for CheckpointManager feature rollback logic."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.cm = CheckpointManager(checkpoints_dir=Path(self.temp_dir))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_checkpoint_lifecycle(self):
        # Build mock feature tree: Origin -> Sketch1 -> Boss-Extrude1
        f3 = DummyFeature("Boss-Extrude1", None)
        f2 = DummyFeature("Sketch1", f3)
        f1 = DummyFeature("Origin", f2)
        doc = DummyModelDoc("PartTest", [f1, f2, f3])

        # Create checkpoint at Boss-Extrude1
        res = self.cm.create_checkpoint(doc, "baseline", "Initial body created")
        self.assertTrue(res["success"])
        self.assertEqual(res["checkpoint"]["last_feature_name"], "Boss-Extrude1")

        # List checkpoints
        cps = self.cm.list_checkpoints("PartTest")
        self.assertEqual(len(cps), 1)
        self.assertEqual(cps[0]["name"], "baseline")


if __name__ == "__main__":
    unittest.main()
