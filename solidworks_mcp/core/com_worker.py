"""
Single-Threaded Apartment (STA) COM Worker
------------------------------------------
Guarantees thread-affinity and safe sequential execution of SolidWorks COM calls.

SolidWorks COM API is Single-Threaded Apartment (STA). Accessing COM objects across
different worker threads without marshaling causes RPC_E_WRONG_THREAD (0x8001010E)
or silent corruption. This module provides a dedicated worker thread that initializes
COM via CoInitialize() and processes all COM operations sequentially through a queue.
"""

import asyncio
import concurrent.futures
import logging
import queue
import sys
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Attempt importing pythoncom
try:
    import pythoncom
    import win32com.client
    import pywintypes
    HAS_PYWIN32 = True
except ImportError:
    HAS_PYWIN32 = False


class AbandonedCallRunningException(Exception):
    """
    Raised when attempting to execute a COM call while an earlier abandoned
    or timed-out COM call is still running on the STA thread.
    """
    def __init__(self, message: str, abandoned_tool: str = "", age: float = 0.0):
        super().__init__(message)
        self.abandoned_tool = abandoned_tool
        self.age = age


class SolidWorksModalBlockedException(Exception):
    """
    Raised when SolidWorks is blocked by an active modal dialog/popup.

    LIFECYCLE & RETRY ARCHITECTURAL BEHAVIOR:
    -----------------------------------------
    1. STA Worker Queue Remains Blocked:
       When a COM call inside the STA worker triggers a modal dialog, the thread
       blocks synchronously inside the COM runtime message pump. Raising this
       exception in the caller does NOT unblock the STA thread itself; the worker
       remains paused at that exact instruction until the dialog is closed.
    2. Post-Dismissal Execution:
       Once the dialog is dismissed out-of-band (via `dismiss_dialog` over Win32
       or manual user interaction), the original COM call resumes and completes.
    3. Client Retry / Double-Execution Warning:
       Because the original call still completes once unblocked, an immediate
       client retry without inspection can cause DOUBLE-EXECUTION (e.g., adding
       two identical features or components). Callers should query the model
       state (`inspect_model`, `list_features`) after dismissal before retrying.
    """
    def __init__(self, message: str, dialog_info: Optional[Dict[str, Any]] = None, dispatched: bool = True):
        super().__init__(message)
        self.dialog_info = dialog_info or {}
        self.dispatched = dispatched


def find_blocking_modal_dialog() -> Optional[Dict[str, Any]]:
    """
    Enumerate SLDWORKS.exe windows across all desktops
    and detect if any modal popup dialog is currently blocking SolidWorks.
    Delegates to dialog_manager with owner window and enabled state verification.
    """
    if not HAS_PYWIN32:
        return None

    try:
        from ..utils.dialog_manager import find_blocking_modal_dialog as dm_find_blocking
        return dm_find_blocking()
    except Exception as e:
        logger.debug(f"Watchdog dialog scan error: {e}")

    return None


class STAWorker:
    """
    Dedicated background worker thread running inside an STA COM apartment.
    All SolidWorks COM API operations must be dispatched through this worker.
    """

    def __init__(self, name: str = "SolidWorksSTAWorker"):
        self._name = name
        self._queue: queue.Queue = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._started = threading.Event()
        self._lock = threading.RLock()
        self._thread_id: Optional[int] = None
        self._current_op: Optional[Dict[str, Any]] = None
        self._abandoned_call: Optional[Dict[str, Any]] = None
        self._op_lock = threading.Lock()

    def start(self) -> None:
        """Start the STA worker thread if not already running."""
        with self._lock:
            if self._running and self._thread and self._thread.is_alive():
                return

            self._started.clear()
            self._running = True
            self._thread = threading.Thread(
                target=self._worker_loop,
                name=self._name,
                daemon=True,
            )
            self._thread.start()
            if not self._started.wait(timeout=10.0):
                self._running = False
                raise TimeoutError("Failed to start STA COM worker thread within 10 seconds.")

    def _worker_loop(self) -> None:
        """Main loop of the STA worker thread."""
        self._thread_id = threading.get_ident()
        logger.info(f"STA COM worker thread started (TID={self._thread_id})")

        if HAS_PYWIN32:
            try:
                pythoncom.CoInitialize()
                logger.debug("CoInitialize() succeeded on STA worker thread.")
            except Exception as e:
                logger.error(f"CoInitialize() failed on STA worker thread: {e}")

        self._started.set()

        try:
            while self._running:
                try:
                    item = self._queue.get(timeout=0.2)
                except queue.Empty:
                    continue

                if item is None:
                    # Sentinel to terminate
                    try:
                        self._queue.task_done()
                    except Exception:
                        pass
                    break

                func, args, kwargs, future, tool_name = item
                if future.cancelled():
                    try:
                        self._queue.task_done()
                    except Exception:
                        pass
                    continue

                with self._op_lock:
                    self._current_op = {
                        "tool": tool_name,
                        "start_time": time.time(),
                        "future": future,
                    }

                try:
                    res = func(*args, **kwargs)
                    future.set_result(res)
                except BaseException as ex:
                    future.set_exception(ex)
                finally:
                    with self._op_lock:
                        was_abandoned = self._abandoned_call is not None
                        self._current_op = None
                        self._abandoned_call = None
                    if was_abandoned:
                        logger.info(f"STA worker completed previously abandoned call '{tool_name}'.")
                    try:
                        self._queue.task_done()
                    except Exception:
                        pass
        finally:
            if HAS_PYWIN32:
                try:
                    pythoncom.CoUninitialize()
                    logger.debug("CoUninitialize() completed on STA worker thread.")
                except Exception as e:
                    logger.warning(f"CoUninitialize() warning: {e}")

            logger.info("STA COM worker thread terminated cleanly.")
            if self._thread_id == threading.get_ident():
                self._thread_id = None

    def submit(
        self,
        func: Callable[..., Any],
        *args: Any,
        tool_name: str = "tool",
        **kwargs: Any,
    ) -> concurrent.futures.Future:
        """Submit a callable to the STA worker queue and return a Future."""
        if not self._running or not self._thread or not self._thread.is_alive():
            self.start()

        with self._op_lock:
            if self._abandoned_call is not None:
                age = time.time() - self._abandoned_call["start_time"]
                abandoned_tool = self._abandoned_call.get("tool", "unknown")
                raise AbandonedCallRunningException(
                    f"Worker is busy with abandoned call '{abandoned_tool}', age {int(age)}s",
                    abandoned_tool=abandoned_tool,
                    age=age,
                )

        # If already on the STA thread, execute directly to prevent deadlock
        if threading.get_ident() == self._thread_id:
            fut: concurrent.futures.Future = concurrent.futures.Future()
            try:
                res = func(*args, **kwargs)
                fut.set_result(res)
            except BaseException as ex:
                fut.set_exception(ex)
            return fut

        fut = concurrent.futures.Future()
        self._queue.put((func, args, kwargs, fut, tool_name))
        return fut

    def run_sync(
        self,
        func: Callable[..., Any],
        *args: Any,
        timeout: float = 60.0,
        watchdog_timeout: float = 4.0,
        tool_name: str = "tool",
        **kwargs: Any,
    ) -> Any:
        """
        Synchronously execute a callable on the STA thread with a timeout and modal dialog watchdog.
        If execution exceeds watchdog_timeout seconds and a modal dialog is detected, raises
        SolidWorksModalBlockedException immediately rather than freezing.
        """
        # If already on the STA thread, execute directly
        if threading.get_ident() == self._thread_id:
            return func(*args, **kwargs)

        fut = self.submit(func, *args, tool_name=tool_name, **kwargs)
        start_time = time.time()

        while True:
            try:
                return fut.result(timeout=0.25)
            except concurrent.futures.TimeoutError:
                pass
            except Exception:
                raise

            elapsed = time.time() - start_time
            if elapsed >= watchdog_timeout:
                dlg = find_blocking_modal_dialog()
                if dlg:
                    with self._op_lock:
                        self._abandoned_call = {"tool": tool_name, "start_time": start_time}
                    title = dlg.get("title", "Modal Dialog")
                    msgs = dlg.get("messages", [])
                    details = f"{title}: {' '.join(msgs)}" if msgs else title
                    raise SolidWorksModalBlockedException(
                        f"SolidWorks is blocked by a modal dialog: {details}",
                        dlg,
                    )

            if elapsed >= timeout:
                with self._op_lock:
                    self._abandoned_call = {"tool": tool_name, "start_time": start_time}
                dlg = find_blocking_modal_dialog()
                if dlg:
                    title = dlg.get("title", "Modal Dialog")
                    raise SolidWorksModalBlockedException(
                        f"SolidWorks is blocked by a modal dialog: {title}",
                        dlg,
                    )
                raise TimeoutError(f"STA COM worker call for tool '{tool_name}' timed out after {timeout} seconds.")

    async def run_async(
        self,
        func: Callable[..., Any],
        *args: Any,
        timeout: float = 60.0,
        watchdog_timeout: float = 4.0,
        tool_name: str = "tool",
        **kwargs: Any,
    ) -> Any:
        """Asynchronously execute a callable on the STA thread with modal dialog watchdog."""
        fut = self.submit(func, *args, tool_name=tool_name, **kwargs)
        start_time = time.time()

        while not fut.done():
            await asyncio.sleep(0.25)
            elapsed = time.time() - start_time
            if elapsed >= watchdog_timeout:
                dlg = find_blocking_modal_dialog()
                if dlg:
                    with self._op_lock:
                        self._abandoned_call = {"tool": tool_name, "start_time": start_time}
                    title = dlg.get("title", "Modal Dialog")
                    msgs = dlg.get("messages", [])
                    details = f"{title}: {' '.join(msgs)}" if msgs else title
                    raise SolidWorksModalBlockedException(
                        f"SolidWorks is blocked by a modal dialog: {details}",
                        dlg,
                    )

            if elapsed >= timeout:
                with self._op_lock:
                    self._abandoned_call = {"tool": tool_name, "start_time": start_time}
                dlg = find_blocking_modal_dialog()
                if dlg:
                    title = dlg.get("title", "Modal Dialog")
                    raise SolidWorksModalBlockedException(
                        f"SolidWorks is blocked by a modal dialog: {title}",
                        dlg,
                    )
                raise TimeoutError(f"STA COM worker call for tool '{tool_name}' timed out after {timeout} seconds.")

        return fut.result()

    @property
    def is_abandoned_call_running(self) -> bool:
        """Check if an earlier abandoned/timed-out call is still running on the STA thread."""
        with self._op_lock:
            return self._abandoned_call is not None

    @property
    def abandoned_call_info(self) -> Optional[Dict[str, Any]]:
        """Return information about the currently running abandoned call, if any."""
        with self._op_lock:
            if self._abandoned_call is None:
                return None
            return dict(self._abandoned_call)

    @property
    def is_alive(self) -> bool:
        """Check if worker thread is active."""
        return bool(self._running and self._thread and self._thread.is_alive())

    @property
    def thread_id(self) -> Optional[int]:
        """Return the native thread ID of the STA worker."""
        return self._thread_id

    def stop(self, timeout: float = 5.0) -> None:
        """Request the worker thread to shutdown cleanly."""
        with self._lock:
            if not self._running:
                return
            self._running = False
            self._queue.put(None)
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=timeout)
            self._thread = None

    def reset(self) -> None:
        """
        Forcefully reset the STA worker if an abandoned call is permanently hung in COM.
        Abandons the hung thread without terminating SolidWorks, drains the queue,
        clears abandoned call states, and initializes a fresh STA worker thread.

        Operational Notes:
        (a) Abandoned Mutation Risk: The abandoned call on the orphaned thread may still
            complete later if SolidWorks unblocks, and may mutate the model. Clients
            must inspect model/document state before retrying mutating tools.
        (b) Modal Blocking Propagation: If SolidWorks itself is still blocked by a modal dialog,
            tool calls dispatched to the newly created STA worker will also detect the modal
            dialog and block/raise swModalDialogBlocking until the dialog is dismissed via Win32.
        """
        with self._lock:
            logger.warning("Forcing STA worker reset: abandoning old thread and starting fresh STA apartment.")
            self._running = False
            while not self._queue.empty():
                try:
                    item = self._queue.get_nowait()
                    if item:
                        _, _, _, fut, _ = item
                        if not fut.done():
                            fut.set_exception(RuntimeError("STA worker was forcefully reset."))
                except Exception:
                    break
            try:
                self._queue.put_nowait(None)
            except Exception:
                pass
            with self._op_lock:
                self._current_op = None
                self._abandoned_call = None
            self._queue = queue.Queue()
            self._thread = None
            self._thread_id = None
            self.start()


# Process-wide singleton
_GLOBAL_STA_WORKER: Optional[STAWorker] = None
_GLOBAL_WORKER_LOCK = threading.Lock()


def get_sta_worker() -> STAWorker:
    """Get or initialize the global STA COM worker singleton."""
    global _GLOBAL_STA_WORKER
    with _GLOBAL_WORKER_LOCK:
        if _GLOBAL_STA_WORKER is None:
            _GLOBAL_STA_WORKER = STAWorker()
            _GLOBAL_STA_WORKER.start()
        elif not _GLOBAL_STA_WORKER.is_alive:
            _GLOBAL_STA_WORKER.start()
        return _GLOBAL_STA_WORKER


def reset_sta_worker() -> STAWorker:
    """Forcefully reset and restart the global STA COM worker singleton."""
    global _GLOBAL_STA_WORKER
    with _GLOBAL_WORKER_LOCK:
        if _GLOBAL_STA_WORKER is not None:
            try:
                _GLOBAL_STA_WORKER.reset()
            except Exception as e:
                logger.warning(f"Error resetting old STA worker: {e}")
        _GLOBAL_STA_WORKER = STAWorker()
        _GLOBAL_STA_WORKER.start()
        return _GLOBAL_STA_WORKER

