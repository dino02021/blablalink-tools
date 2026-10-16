"""Cooperative shared SDK access and exclusive offline mock maintenance."""

from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import math
import os
import threading
import time

from adapters.mock_d1_workspace import require_mock_d1_workspace

_STATE = threading.local()


@contextmanager
def mock_d1_access(directory, schema_id, *, is_exclusive=False, timeout=60):
    if type(is_exclusive) is not bool or type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 60:
        raise ValueError('bounded mock D1 lock mode and timeout required')
    directory = require_mock_d1_workspace(directory, schema_id)
    key = os.path.normcase(str(directory))
    held = getattr(_STATE, 'held', set())
    if key in held:
        raise ValueError('nested mock D1 access is unsupported')
    path = directory / '.sdk-access.lock'
    if path.is_symlink():
        raise ValueError('mock D1 lock cannot be redirected')
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_BINARY', 0), 0o600)
    with os.fdopen(descriptor, 'r+b', buffering=0) as handle:
        deadline = time.monotonic() + timeout
        token = None
        while token is None:
            token = _try_lock(handle, is_exclusive)
            if token is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('mock_d1_access_timeout')
                time.sleep(min(.01, remaining))
        try:
            # A restore/maintenance fence may have appeared while waiting.
            require_mock_d1_workspace(directory, schema_id)
            held.add(key)
            _STATE.held = held
            try:
                yield directory
            finally:
                held.remove(key)
        finally:
            _unlock(handle, token)


def _try_lock(handle, is_exclusive):
    if os.name == 'nt':
        import msvcrt
        api, overlap_type = _windows_api()
        overlap = overlap_type()
        native = msvcrt.get_osfhandle(handle.fileno())
        if api.LockFileEx(native, 1 | (2 if is_exclusive else 0), 0, 1, 0, ctypes.byref(overlap)):
            return (api, overlap, native)
        error = ctypes.get_last_error()
        if error == 33:  # ERROR_LOCK_VIOLATION: nonblocking contention only.
            return None
        raise ctypes.WinError(error)
    if os.name == 'posix':
        import errno
        import fcntl
        try:
            fcntl.flock(handle.fileno(), (fcntl.LOCK_EX if is_exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            return True
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN):
                return None
            raise
    raise RuntimeError('mock D1 OS lock is unsupported')


def _unlock(handle, token):
    if os.name == 'nt':
        api, overlap, native = token
        if not api.UnlockFileEx(native, 0, 1, 0, ctypes.byref(overlap)):
            raise ctypes.WinError(ctypes.get_last_error())
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _windows_api():
    class Overlapped(ctypes.Structure):
        _fields_ = [('Internal', ctypes.c_size_t), ('InternalHigh', ctypes.c_size_t),
                    ('Offset', wintypes.DWORD), ('OffsetHigh', wintypes.DWORD), ('hEvent', wintypes.HANDLE)]
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(Overlapped)]
    api.LockFileEx.restype = wintypes.BOOL
    api.UnlockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(Overlapped)]
    api.UnlockFileEx.restype = wintypes.BOOL
    return api, Overlapped
