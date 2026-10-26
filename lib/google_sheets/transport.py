"""Bound Sheets requests shared by clients using the same service account."""
from __future__ import annotations

from collections import deque
from email.utils import parsedate_to_datetime
import random
import threading
import time
from urllib.parse import urlsplit

from google.auth.transport.requests import AuthorizedSession
import requests


_limiters = {}
_limiters_lock = threading.Lock()


def get_request_limiter(project_id, service_account_email):
    key = (project_id, service_account_email)
    with _limiters_lock:
        if key not in _limiters:
            _limiters[key] = RequestLimiter()
        return _limiters[key]


class RequestLimiter:
    def __init__(self, limit=50, period=60.0, clock=time.monotonic, sleep=time.sleep):
        self._limit, self._period = limit, period
        self._clock, self._sleep = clock, sleep
        self._events = {True: deque(), False: deque()}
        self._lock = threading.Lock()

    def acquire(self, is_read):
        while True:
            with self._lock:
                now = self._clock()
                events = self._events[is_read]
                while events and events[0] <= now - self._period:
                    events.popleft()
                if len(events) < self._limit:
                    events.append(now)
                    return
                delay = max(0.01, events[0] + self._period - now)
            self._sleep(delay)


class SheetsSession(AuthorizedSession):
    def __init__(self, credentials, limiter, sleep=time.sleep, jitter=random.random):
        super().__init__(credentials)
        self._limiter, self._sleep, self._jitter = limiter, sleep, jitter

    def request(self, method, url, **kwargs):
        if urlsplit(url).hostname != "sheets.googleapis.com":
            return self._request_once(method, url, **kwargs)
        is_read = method.upper() in {"GET", "HEAD"}
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = (10, 190)
        for attempt in range(7):
            self._limiter.acquire(is_read)
            try:
                response = self._request_once(method, url, **kwargs)
            except (requests.Timeout, requests.ConnectionError):
                # A write might already have committed. Its caller must verify it.
                if not is_read or attempt == 6:
                    raise
                self._sleep(min(2 ** attempt, 32) + self._jitter())
                continue
            is_retryable = response.status_code == 429 or (is_read and response.status_code in {500, 502, 503, 504})
            if not is_retryable or attempt == 6:
                return response
            delay = max(min(2 ** attempt, 32) + self._jitter(), _retry_after_seconds(response.headers.get("Retry-After")))
            response.close()
            self._sleep(delay)
        raise RuntimeError("Sheets retry limit reached")

    def _request_once(self, method, url, **kwargs):
        return super().request(method, url, **kwargs)


def _retry_after_seconds(value):
    if not value:
        return 0.0
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
        except (TypeError, ValueError, OverflowError):
            return 0.0
