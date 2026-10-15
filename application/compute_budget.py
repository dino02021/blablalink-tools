"""Cooperative local compute limits; provider billing reservations remain separate."""

import math
import time


class ComputeLimit(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class ComputeExecutionCheck:
    def __init__(self, store, lease, budget):
        self.store, self.lease, self.budget = store, lease, budget
        self.heartbeat_at = budget.started_at - 15

    def __call__(self):
        self.budget.check()
        if self.budget.last_checked_at - self.heartbeat_at >= 15:
            self.store.heartbeat(self.lease)
            self.heartbeat_at = self.budget.last_checked_at


class ComputeBudget:
    def __init__(self, seconds: float = 240, reserve_seconds: float = 10, max_checks: int = 500000,
                 memory_limit_bytes: int | None = None, memory_reader=None, clock=time.monotonic):
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in (seconds, reserve_seconds)):
            raise ValueError("finite duration required")
        if not 0 <= reserve_seconds < seconds or type(max_checks) is not int or max_checks <= 0:
            raise ValueError("positive compute budget required")
        if memory_limit_bytes is not None and (type(memory_limit_bytes) is not int or memory_limit_bytes <= 0 or memory_reader is None):
            raise ValueError("memory budget requires an explicit usage reader")
        self.seconds = seconds
        self.reserve_seconds = reserve_seconds
        self.max_checks = max_checks
        self.memory_limit_bytes = memory_limit_bytes
        self.memory_reader = memory_reader
        self.clock = clock
        self.started_at = clock()
        self.last_checked_at = self.started_at
        self.checks = 0
        if type(self.started_at) not in (int, float) or not math.isfinite(self.started_at):
            raise ValueError("invalid monotonic clock")

    def check(self) -> None:
        now = self.clock()
        if type(now) not in (int, float) or not math.isfinite(now) or now < self.last_checked_at:
            raise ComputeLimit("compute_clock_invalid")
        self.last_checked_at = now
        self.checks += 1
        if now - self.started_at >= self.seconds - self.reserve_seconds:
            raise ComputeLimit("compute_deadline")
        if self.checks > self.max_checks:
            raise ComputeLimit("compute_operation_limit")
        if self.memory_limit_bytes is not None:
            try:
                used = self.memory_reader()
            except Exception as error:
                raise ComputeLimit("compute_memory_unknown") from error
            if type(used) is not int or used < 0:
                raise ComputeLimit("compute_memory_unknown")
            if used >= self.memory_limit_bytes:
                raise ComputeLimit("compute_memory_limit")
