"""Read this process's resident memory using local operating-system facilities."""

import os
from functools import lru_cache
from pathlib import Path
import sys

from application.compute_budget import ComputeBudget


def resident_bytes() -> int:
    if sys.platform == "win32":
        return _windows_resident_bytes()
    if sys.platform.startswith("linux"):
        fields = Path("/proc/self/statm").read_text(encoding="ascii").split()
        return int(fields[1]) * os.sysconf("SC_PAGE_SIZE")
    # getrusage is a conservative peak fallback on other Unix platforms.
    import resource
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1024)


def local_compute_budget() -> ComputeBudget:
    return ComputeBudget(memory_limit_bytes=512 * 1024 * 1024, memory_reader=resident_bytes)


def _windows_resident_bytes() -> int:
    import ctypes
    kernel, psapi, counter_type = _windows_memory_api()
    counters = counter_type()
    counters.cb = ctypes.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise OSError(ctypes.get_last_error(), "local process memory read failed")
    return int(counters.WorkingSetSize)


@lru_cache(maxsize=1)
def _windows_memory_api():
    import ctypes
    from ctypes import wintypes

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessMemoryCounters), wintypes.DWORD)
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    return kernel, psapi, ProcessMemoryCounters
