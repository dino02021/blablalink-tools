import pytest

from adapters.process_memory import local_compute_budget, resident_bytes
from application.compute_budget import ComputeLimit


def test_real_local_process_reader_returns_positive_bytes():
    assert type(resident_bytes()) is int
    assert resident_bytes() > 0
    budget = local_compute_budget()
    assert budget.memory_limit_bytes == 512 * 1024 * 1024
    assert budget.memory_reader is resident_bytes


def test_reader_failure_is_capacity_signal_not_zero_memory(monkeypatch):
    monkeypatch.setattr("adapters.process_memory.resident_bytes", failing_reader)
    with pytest.raises(ComputeLimit, match="compute_memory_unknown"):
        local_compute_budget().check()


def test_real_memory_reader_can_enforce_explicit_small_limit():
    budget = local_compute_budget()
    budget.memory_limit_bytes = 1
    with pytest.raises(ComputeLimit, match="compute_memory_limit"):
        budget.check()


def test_repeated_local_memory_checks_do_not_allocate_unbounded_native_types():
    before = resident_bytes()
    for _ in range(10000):
        assert resident_bytes() > 0
    assert resident_bytes() - before < 32 * 1024 * 1024


def failing_reader():
    raise OSError("synthetic OS read failure")
