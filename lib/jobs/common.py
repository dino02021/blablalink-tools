# -*- coding: utf-8 -*-
"""Common helpers for headless jobs."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict


@dataclass
class JobResult:
    ok: bool
    message: str = ""
    stats: Dict[str, Any] = field(default_factory=dict)
    outputs: Dict[str, Any] = field(default_factory=dict)


def run_coro_sync(coro_factory: Callable[[], Awaitable[Any]]) -> Any:
    """Run an async coroutine synchronously in a fresh event loop."""
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(coro_factory())
        loop.run_until_complete(loop.shutdown_asyncgens())
        return result
    finally:
        asyncio.set_event_loop(None)
        loop.close()


def resolve_account(account: str | None) -> str:
    value = (account or "").strip()
    if value:
        return value
    return str(os.getenv("BLABLALINK_DEFAULT_ACCOUNT", "")).strip()
