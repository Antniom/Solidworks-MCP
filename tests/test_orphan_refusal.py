"""
Unit/Integration test: Orphan Refusal Semantics (Requirement 3)
Marked opt-in via pytest marker and environment variable `RUN_ORPHAN_TEST`.
Skipped by default so that standard test suites do not create unmanaged orphaned processes.
"""

import os
import sys
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from solidworks_mcp.automation import find_sldworks_pids

@pytest.mark.orphan_creation
@pytest.mark.skipif(
    os.environ.get("RUN_ORPHAN_TEST") != "1",
    reason="Orphan creation test is opt-in (requires RUN_ORPHAN_TEST=1) to prevent leaking unmanaged processes in default suite"
)
def test_unattachable_orphan_refusal():
    from scripts.test_unattachable_orphans import main
    main()
