"""Optional cooperative execution checks, with no clock, platform or I/O dependency."""

from contextlib import contextmanager
from contextvars import ContextVar

_CHECK = ContextVar("offline_compute_check", default=None)


def check_execution() -> None:
    check = _CHECK.get()
    if check is not None:
        check()


@contextmanager
def execution_checks(check):
    token = _CHECK.set(check)
    try:
        yield
    finally:
        _CHECK.reset(token)
