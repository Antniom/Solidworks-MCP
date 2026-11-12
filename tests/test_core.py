"""
Unit Tests for SolidWorks MCP Core Services
-------------------------------------------
Tests response envelopes, STA COM worker execution, filesystem policies,
and capabilities reporting without requiring a live SolidWorks instance.
"""

import os
import threading
import unittest

from solidworks_mcp.core.envelope import (
    ResponseEnvelope,
    success_envelope,
    error_envelope,
    create_envelope,
)
from solidworks_mcp.core.com_worker import STAWorker
from solidworks_mcp.core.fs_policy import FileSystemPolicy
from solidworks_mcp.core.capabilities import CapabilityInspector
from solidworks_mcp.constants import SwErrors


class TestResponseEnvelope(unittest.TestCase):
    """Tests for typed structured response envelopes."""

    def test_success_envelope(self):
        env = success_envelope(
            message="Feature created",
            data={"feature_name": "Boss-Extrude1"},
            duration_ms=42.5,
            sw_version="33.5.0",
            unit="mm",
        )
        self.assertTrue(env.success)
        self.assertEqual(env.message, "Feature created")
        self.assertTrue(env.operation_id.startswith("op_"))
        self.assertEqual(env.data["feature_name"], "Boss-Extrude1")
        self.assertEqual(env.diagnostics.unit, "mm")
        self.assertIsNone(env.error)

        # Serialization
        as_dict = env.to_dict()
        self.assertTrue(as_dict["success"])
        self.assertIn("operation_id", as_dict)
        self.assertIn("diagnostics", as_dict)

        mcp_text = env.to_mcp_text()
        self.assertIn("[SUCCESS]", mcp_text)
        self.assertIn("Boss-Extrude1", mcp_text)

    def test_error_envelope(self):
        env = error_envelope(
            message="Selection failed",
            error_code=int(SwErrors.swSelectionError),
            error_name="swSelectionError",
            domain="sketch",
            details="Plane not found in model",
            recovery_hint="Check plane name with list_features",
        )
        self.assertFalse(env.success)
        self.assertIsNotNone(env.error)
        self.assertEqual(env.error.code, int(SwErrors.swSelectionError))
        self.assertEqual(env.error.domain, "sketch")
        self.assertEqual(env.error.recovery_hint, "Check plane name with list_features")

        mcp_text = env.to_mcp_text()
        self.assertIn("[ERROR]", mcp_text)
        self.assertIn("Recovery Hint", mcp_text)

    def test_create_envelope_from_legacy(self):
        legacy = {
            "success": True,
            "message": "Part created",
            "data": {"name": "Part1"},
        }
        env = create_envelope(legacy)
        self.assertTrue(env.success)
        self.assertEqual(env.data["name"], "Part1")


class TestSTAWorker(unittest.TestCase):
    """Tests for the STA COM worker thread dispatching."""

    def setUp(self):
        self.worker = STAWorker(name="TestSTAWorker")
        self.worker.start()

    def tearDown(self):
        self.worker.stop()

    def test_worker_thread_isolation(self):
        main_tid = threading.get_ident()
        worker_tid = self.worker.run_sync(lambda: threading.get_ident())

        self.assertNotEqual(main_tid, worker_tid)
        self.assertEqual(worker_tid, self.worker.thread_id)

    def test_worker_exception_propagation(self):
        def failing_task():
            raise ValueError("Test error inside STA worker")

        with self.assertRaises(ValueError) as ctx:
            self.worker.run_sync(failing_task)
        self.assertIn("Test error inside STA worker", str(ctx.exception))


class TestFileSystemPolicy(unittest.TestCase):
    """Tests for safe filesystem boundaries and extension validation."""

    def setUp(self):
        self.policy = FileSystemPolicy()

    def test_allowed_extensions(self):
        valid, path, err = self.policy.validate_path("model.sldprt")
        self.assertTrue(valid)
        self.assertIsNone(err)

        valid_step, _, err_step = self.policy.validate_path("assembly.step")
        self.assertTrue(valid_step)

        # Prohibited extension
        invalid, _, err_bad = self.policy.validate_path("malicious.exe")
        self.assertFalse(invalid)
        self.assertIn("Unsupported file extension", err_bad)

    def test_empty_filepath(self):
        valid, _, err = self.policy.validate_path("")
        self.assertFalse(valid)
        self.assertIn("Filepath cannot be empty", err)


class TestCapabilityInspector(unittest.TestCase):
    """Tests capability inspection in offline mode."""

    def test_offline_capabilities(self):
        caps = CapabilityInspector.get_system_capabilities(sw_app=None)
        self.assertIn("installation", caps)
        self.assertIn("runtime", caps)
        self.assertFalse(caps["runtime"]["connected"])
        self.assertIn("addins", caps)


if __name__ == "__main__":
    unittest.main()
