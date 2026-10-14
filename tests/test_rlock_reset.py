"""
Unit Test: RLock Reentrancy & Deadlock Fix in STAWorker (Commit 18b7302 / Requirement 4)
Verifies that STAWorker._lock is a reentrant lock (threading.RLock) so that
reset() holding _lock can call start() without self-deadlock.
"""

import threading
import time
import pytest
import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from solidworks_mcp.core.com_worker import STAWorker

def test_staworker_lock_is_rlock():
    """Verify that STAWorker._lock is an instance of RLock to prevent self-deadlock."""
    worker = STAWorker("TestWorkerLock")
    # Verify lock type is RLock
    rlock_type = type(threading.RLock())
    assert isinstance(worker._lock, rlock_type), f"Expected RLock, got {type(worker._lock)}"

def test_staworker_reentrant_acquisition():
    """Verify that start() and reset() can be called while holding _lock without deadlocking."""
    worker = STAWorker("TestWorkerReentrancy")
    acquired = False

    # Simulate reentrant lock acquisition: holding _lock while calling start()
    with worker._lock:
        worker.start()
        acquired = True

    assert acquired, "Failed to re-enter _lock during start()"
    assert worker._running is True
    assert worker._thread is not None

    # Test reset() execution speed (must complete promptly in < 2s, not hang indefinitely)
    t0 = time.perf_counter()
    worker.reset()
    dur = time.perf_counter() - t0

    assert dur < 2.0, f"reset() took too long ({dur:.2f}s), possible deadlock!"
    assert worker._running is True
    assert worker._current_op is None
    assert worker._abandoned_call is None

    worker.stop(timeout=2.0)

if __name__ == "__main__":
    test_staworker_lock_is_rlock()
    test_staworker_reentrant_acquisition()
    print("ALL RLock deadlock tests passed!")
