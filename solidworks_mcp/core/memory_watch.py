"""
SolidWorks memory watch (MCP-050)
---------------------------------
SolidWorks does not give back all memory when documents are closed: after a long session of
creating, inserting, checking and closing parts it reached 10 GB private memory on a 7.4 GB machine
and terminated with "SOLIDWORKS is out of memory". These helpers read the SolidWorks process'
private memory and the system's free memory with plain Win32 calls (no psutil dependency), so tool
results can warn early and `restart_solidworks` can report what a restart freed.
"""

import ctypes
import ctypes.wintypes as wt
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0

# Warn when SolidWorks holds more than this share of physical RAM, or free RAM drops below the floor.
WARN_SHARE_OF_RAM = 0.5
WARN_FREE_MB = 800.0


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", wt.DWORD),
        ("dwMemoryLoad", wt.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class _PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [
        ("cb", wt.DWORD),
        ("PageFaultCount", wt.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


def _kernel32():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = wt.HANDLE
    k.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    k.CloseHandle.argtypes = [wt.HANDLE]
    k.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
    k.WaitForSingleObject.restype = wt.DWORD
    k.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
    k.K32GetProcessMemoryInfo.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD]
    return k


def solidworks_pid(app: Any, known_pid: Optional[int] = None) -> Optional[int]:
    if known_pid:
        return int(known_pid)
    try:
        pid = app.GetProcessID
        pid = pid() if callable(pid) else pid
        return int(pid) if pid else None
    except Exception:
        return None


def memory_status(pid: Optional[int]) -> Dict[str, Any]:
    """{'solidworks_private_mb', 'system_total_mb', 'system_free_mb', 'warning'} (values may be None)."""
    out: Dict[str, Any] = {"solidworks_private_mb": None, "system_total_mb": None, "system_free_mb": None,
                           "warning": None}
    try:
        k = _kernel32()
        ms = _MEMORYSTATUSEX()
        ms.dwLength = ctypes.sizeof(ms)
        if k.GlobalMemoryStatusEx(ctypes.byref(ms)):
            out["system_total_mb"] = round(ms.ullTotalPhys / 1048576.0)
            out["system_free_mb"] = round(ms.ullAvailPhys / 1048576.0)
        if pid:
            h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if h:
                try:
                    pmc = _PROCESS_MEMORY_COUNTERS_EX()
                    pmc.cb = ctypes.sizeof(pmc)
                    if k.K32GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
                        out["solidworks_private_mb"] = round(pmc.PrivateUsage / 1048576.0)
                finally:
                    k.CloseHandle(h)
        priv, total, free = out["solidworks_private_mb"], out["system_total_mb"], out["system_free_mb"]
        if priv is not None and total and (priv > WARN_SHARE_OF_RAM * total or (free is not None and free < WARN_FREE_MB)):
            out["warning"] = (
                f"SolidWorks is using {priv / 1024:.1f} GB of {total / 1024:.1f} GB RAM "
                f"({free / 1024:.1f} GB free). SolidWorks does not release memory from closed documents; "
                "save your work and call restart_solidworks before continuing, or it may run out of memory "
                "and terminate (MCP-050).")
    except Exception as ex:
        logger.debug(f"memory_status failed: {ex}")
    return out


def wait_for_exit(pid: int, timeout_s: float, terminate: bool) -> str:
    """'exited', 'terminated' or 'still running'."""
    try:
        k = _kernel32()
        h = k.OpenProcess(SYNCHRONIZE | PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return "exited"  # cannot open: the process is gone
        try:
            if k.WaitForSingleObject(h, int(timeout_s * 1000)) == WAIT_OBJECT_0:
                return "exited"
            if terminate:
                k.TerminateProcess(h, 1)
                if k.WaitForSingleObject(h, 30000) == WAIT_OBJECT_0:
                    return "terminated"
            return "still running"
        finally:
            k.CloseHandle(h)
    except Exception as ex:
        logger.debug(f"wait_for_exit failed: {ex}")
        return "still running"
