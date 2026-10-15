"""A private Windows Job Object; one child by default, bounded trusted trees optional."""

from functools import lru_cache


class WindowsChildJob:
    def __init__(self, memory_limit_bytes, max_processes=1):
        import ctypes
        if (type(memory_limit_bytes) is not int or not 32*1024*1024<=memory_limit_bytes<=2*1024**3
                or type(max_processes) is not int or not 1<=max_processes<=4):
            raise ValueError('bounded owned Windows process profile required')
        self.kernel, limit_type = _api()
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError(ctypes.get_last_error(), "cannot create child job")
        try:
            limits = limit_type()
            limits.BasicLimitInformation.LimitFlags = 0x100 | 0x2000 | 0x8
            limits.BasicLimitInformation.ActiveProcessLimit = max_processes
            limits.ProcessMemoryLimit = memory_limit_bytes
            if max_processes>1:
                limits.BasicLimitInformation.LimitFlags |= 0x200
                limits.JobMemoryLimit = memory_limit_bytes
            if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise OSError(ctypes.get_last_error(), "cannot set child job limits")
        except BaseException:
            self.close()
            raise

    def attach(self, process):
        import ctypes
        # Popen owns this exact process handle; do not reopen a possibly reused PID.
        if not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise OSError(ctypes.get_last_error(), "cannot attach owned child job")

    def close(self):
        if self.handle is not None:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


@lru_cache(maxsize=1)
def _api():
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class IOCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", IOCounters), ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    return kernel, ExtendedLimits
