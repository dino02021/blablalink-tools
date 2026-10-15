"""Reentrant thread and OS file locks for the local mock workbook simulator."""

from contextlib import contextmanager
import errno
from functools import wraps
import os
from pathlib import Path
import threading
import time
import weakref

from domain.execution import check_execution

_LOCKS = weakref.WeakValueDictionary()
_REGISTRY_GUARD = threading.Lock()
_THREAD_STATE = threading.local()


def locked_workbook_access(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        subject = args[0] if args else kwargs.get('resource',kwargs.get('plan',kwargs.get('layout')))
        resource = subject if isinstance(subject,str) else getattr(subject,'resource',None)
        if resource is None and hasattr(subject,'before'):
            resource = subject.before.resource
        path = self._path(resource)
        with workbook_file_lock(path):
            return method(self,*args,**kwargs)
    return call


@contextmanager
def workbook_file_lock(path, timeout=2):
    if type(timeout) not in (int,float) or not 0<timeout<=10:
        raise ValueError('bounded mock workbook lock timeout required')
    path=Path(path).resolve()
    key=os.path.normcase(str(path))
    check_execution()
    local=_local_lock(key)
    deadline=time.monotonic()+timeout
    while not local.acquire(blocking=False):
        _wait(deadline)
    try:
        held=getattr(_THREAD_STATE,'held',None)
        if held is None:
            held=set();_THREAD_STATE.held=held
        if key in held:
            yield
            return
        lock_directory=path.parent/'.locks'
        lock_directory.mkdir(exist_ok=True)
        if lock_directory.is_symlink() or lock_directory.resolve().parent!=path.parent:
            raise ValueError('mock workbook lock directory escapes its owner')
        lock_path=lock_directory/(path.name+'.lock')
        if lock_path.is_symlink():raise ValueError('mock workbook lock cannot be a symlink')
        descriptor=os.open(lock_path,os.O_RDWR|os.O_CREAT|getattr(os,'O_BINARY',0),0o600)
        with os.fdopen(descriptor,'r+b',buffering=0) as handle:
            if os.fstat(handle.fileno()).st_size==0:
                handle.write(b'\0')
            while not _try_os_lock(handle):
                _wait(deadline)
            held.add(key)
            try:
                yield
            finally:
                held.remove(key)
                _release_os_lock(handle)
    finally:
        local.release()


def _local_lock(key):
    with _REGISTRY_GUARD:
        value=_LOCKS.get(key)
        if value is None:
            value=threading.RLock();_LOCKS[key]=value
        return value


def _wait(deadline):
    check_execution()
    remaining=deadline-time.monotonic()
    if remaining<=0:raise TimeoutError('mock_workbook_lock_timeout')
    time.sleep(min(0.01,remaining))


def _try_os_lock(handle):
    handle.seek(0)
    try:
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        elif os.name=='posix':
            import fcntl
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        else:
            raise RuntimeError('mock workbook OS lock is unsupported')
        return True
    except OSError as error:
        if error.errno in (errno.EACCES,errno.EAGAIN,errno.EDEADLK) or getattr(error,'winerror',None)==33:
            return False
        raise


def _release_os_lock(handle):
    handle.seek(0)
    if os.name=='nt':
        import msvcrt
        msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
