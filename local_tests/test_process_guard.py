from contextlib import closing
from dataclasses import replace
import sys

import pytest

from adapters.local_state import LocalStateStore
from application.tasks import TaskSpec
from executors.local.demo import run_demo
from executors.local.isolated import run_isolated_worker
from executors.local.process_guard import run_python_module


def test_noncooperative_loop_is_stopped_by_parent_wall_clock():
    result = run_python_module("timeit", ("-n", "1", "-r", "1", "while True: pass"), seconds=0.4)
    assert result["outcome"] == "deadline_terminated" and result["is_child_stopped"]
    assert result["seconds"] < 5


def test_native_memory_limit_stops_single_large_allocation_without_checkpoints():
    result = run_python_module("timeit", ("-n", "1", "-r", "1", "bytearray(256 * 1024 * 1024)"), seconds=5,
        memory_limit_bytes=96*1024*1024)
    assert result["outcome"] == "child_failed" and result["is_child_stopped"]
    assert result["exit_code"] != 0 and "stderr" not in result


def test_child_output_is_bounded_without_buffering_the_entire_stream():
    result = run_python_module("timeit", ("-n", "1", "-r", "1", "print('x'*100000)"), seconds=5, output_limit_bytes=1024)
    assert result["outcome"] in ("child_failed", "output_limit_terminated")
    assert len(result["stdout"].encode()) <= 1024 and result["is_child_stopped"]


def test_worker_child_compute_does_not_publish_or_inherit_provider_credentials(tmp_path, monkeypatch):
    directory = tmp_path / "mock"
    summary = run_demo(directory)
    before_books = {path.name: path.read_bytes() for path in (directory / "workbooks").glob("*.json")}
    monkeypatch.setenv("DISCORD_TOKEN", "must-not-reach-child")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "must-not-reach-child")
    with closing(LocalStateStore(directory / "state.sqlite")) as store:
        original = store.get_job(summary["job_id"])
        spec = replace(original.spec, versions={**original.spec.version_map, "isolated_test": "v1"})
        job = store.submit_job("isolated", spec, is_desired=True)
    result = run_isolated_worker(directory, "mock-release-v1", seconds=10)
    assert result["outcome"] == "completed" and result["is_child_stopped"]
    assert result["child_result"]["processed"][0]["job_id"] == job.job_id
    assert result["child_result"]["processed"][0]["state"] == "computed"
    assert result["network_providers"] == [] and "stdout" not in result
    assert {path.name: path.read_bytes() for path in (directory / "workbooks").glob("*.json")} == before_books


def test_guard_environment_drops_credentials_and_python_injection(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "private")
    monkeypatch.setenv("PYTHONSTARTUP", "private.py")
    statement = "import os; assert 'DISCORD_TOKEN' not in os.environ and 'PYTHONSTARTUP' not in os.environ"
    result = run_python_module("timeit", ("-n", "1", "-r", "1", statement), seconds=5)
    assert result["outcome"] == "completed" and result["is_child_stopped"]


def test_unmarked_or_restore_fenced_workspace_never_starts_child(tmp_path, monkeypatch):
    monkeypatch.setattr("executors.local.isolated.run_python_module", unexpected_child)
    with pytest.raises(ValueError, match="initialized"):
        run_isolated_worker(tmp_path, "code")
    directory = tmp_path / "mock"
    run_demo(directory)
    (directory / "restore-lock.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="fenced"):
        run_isolated_worker(directory, "code")


@pytest.mark.parametrize("changes", [{"seconds": float('nan')}, {"seconds": 0}, {"memory_limit_bytes": 1}, {"output_limit_bytes": 1}])
def test_guard_rejects_unbounded_or_invalid_profiles(changes):
    with pytest.raises(ValueError):
        run_python_module("timeit", (), **changes)


def test_killed_compute_does_not_mark_job_successful_or_force_release_its_lease(tmp_path):
    path = tmp_path / "state.sqlite"
    with closing(LocalStateStore(path, clock=fixed_clock)) as store:
        job = store.submit_job("request", TaskSpec("recompute_healthcheck", "union", "code"), is_desired=True)
    setup = f"from adapters.local_state import LocalStateStore; store=LocalStateStore({str(path)!r},clock=lambda:100.0); lease=store.claim('bounded','code'); assert lease is not None"
    result = run_python_module("timeit", ("-n", "1", "-r", "1", "-s", setup, "while True: pass"), seconds=0.8)
    assert result["outcome"] == "deadline_terminated" and result["is_child_stopped"]
    with closing(LocalStateStore(path, clock=fixed_clock)) as store:
        assert store.get_job(job.job_id).state == "running"
        assert store.get_job(job.job_id).result_id is None
        store.recover()
        assert store.get_job(job.job_id).state == "running"


def fixed_clock():
    return 100.0


@pytest.mark.parametrize("body", ["not-json", "[]", '{"is_mock":true,"processed":[],"network_providers":["external"]}'])
def test_isolated_worker_never_accepts_malformed_child_receipt(tmp_path, monkeypatch, body):
    directory = tmp_path / "mock"
    run_demo(directory)
    monkeypatch.setattr("executors.local.isolated.run_python_module", lambda *args: {
        "outcome": "completed", "stdout": body, "is_child_stopped": True})
    result = run_isolated_worker(directory, "code")
    assert result["outcome"] == "child_protocol_invalid" and result["child_result"] is None
    assert "stdout" not in result


def test_native_fence_setup_failure_never_starts_worker_and_stops_owned_child(monkeypatch):
    if sys.platform != "win32":
        return  # Windows native fence branch; Linux uses bootstrap rlimits.
    FailingFence.processes = []
    monkeypatch.setattr("executors.local.process_guard.WindowsChildJob", FailingFence)
    with pytest.raises(OSError, match="fence"):
        run_python_module("timeit", ("-n", "1", "-r", "1", "while True: pass"), seconds=5)
    assert len(FailingFence.processes) == 1 and FailingFence.processes[0].poll() is not None


class FailingFence:
    processes = []

    def __init__(self, memory):
        pass

    def attach(self, process):
        self.processes.append(process)
        raise OSError("mock native fence setup failed")

    def close(self):
        pass


def unexpected_child(*args, **kwargs):
    raise AssertionError("must not launch child")
