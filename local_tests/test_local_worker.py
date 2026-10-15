from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from adapters.local_raid_plans import LocalRaidPlans
from adapters.local_state import LocalStateStore
from adapters.mock_sheets import MockSheets
from application.compute_budget import ComputeBudget
from application.raid_codec import restore_problem
from application.tasks import TaskSpec
from domain.healthcheck import ELEMENTS
from domain.raid import FixedInputs, RaidMember
from domain.raid_planner import RaidProblem
from domain.snapshots import MemberKey
from executors.local.demo import run_demo
from executors.local.worker import run_worker


@pytest.fixture
def workspace(tmp_path):
    directory = tmp_path / "demo"
    summary = run_demo(directory)
    store = LocalRaidPlans(directory / "state.sqlite")
    yield directory, summary, store
    store.close()


def test_compute_only_then_restart_publishes_sealed_result(workspace):
    directory, summary, store = workspace
    job = submit_new_healthcheck(store, summary)
    before = MockSheets(directory / "workbooks").read("output_demo")
    first = run_worker(directory, "mock-release-v1")
    assert first["processed"][0]["state"] == "computed"
    assert MockSheets(directory / "workbooks").read("output_demo") == before
    result_id = store.get_job(job.job_id).result_id
    second = run_worker(directory, "mock-release-v1", is_publish_enabled=True)
    assert second["processed"][0]["state"] == "succeeded"
    assert store.get_job(job.job_id).result_id == result_id
    assert run_worker(directory, "mock-release-v1", is_publish_enabled=True)["processed"] == []


def test_worker_skips_unsupported_wrong_code_and_hosted_jobs(workspace):
    directory, _, store = workspace
    specs = [TaskSpec("prepare_layout", "union", "code", family="layout"),
        TaskSpec("recompute_healthcheck", "union", "other"),
        TaskSpec("recompute_healthcheck", "union", "code", executor_type="actions")]
    for index, spec in enumerate(specs):
        store.submit_job(f"request_{index}", spec)
    assert run_worker(directory, "code")["processed"] == []
    assert all(job.state == "queued" and job.attempt == 0 for job in store.list_jobs() if job.spec.code_sha != "mock-release-v1")


def test_filter_happens_before_queue_limit(tmp_path):
    store = LocalStateStore(tmp_path / "state.sqlite")
    try:
        for index in range(105):
            store.submit_job(f"req_{index}", TaskSpec("prepare_layout", f"scope_{index}", "code", priority=100))
        supported = store.submit_job("wanted", TaskSpec("recompute_healthcheck", "target", "code"))
        lease = store.claim("worker", "code", commands=("recompute_healthcheck",))
        assert lease.job_id == supported.job_id
    finally:
        store.close()


def test_worker_bad_inputs_fail_symbolically_without_publishing(workspace):
    directory, _, store = workspace
    job = store.submit_job("bad", TaskSpec("recompute_healthcheck", "scope", "code"))
    result = run_worker(directory, "code", is_publish_enabled=True)
    assert result["processed"][0]["error_code"] == "worker_input_invalid"
    assert store.get_job(job.job_id).state == "failed"


def test_raid_load_checkpoint_resume_never_confirms(workspace):
    directory, _, store = workspace
    problem = make_problem()
    assert restore_problem(json.loads(json.dumps(asdict(problem)))) == problem
    store.observe_problem("raid_scope", problem, 0)
    job = store.submit_job("raid", TaskSpec("generate_raid_draft", "raid_scope", "code", {"problem": problem.problem_id}, family="raid"), is_desired=True)
    first = run_worker(directory, "code", budget_factory=tiny_budget)
    assert first["processed"][0]["state"] == "capacity_blocked"
    assert store.load_checkpoint(job.job_id)["stage"] == "start"
    store.resume(job.job_id)
    second = run_worker(directory, "code")
    assert second["processed"][0]["state"] == "succeeded"
    assert store.get_result(store.get_job(job.job_id).result_id)["problem_id"] == problem.problem_id
    assert store.confirmed_head("raid_scope") is None


def test_worker_bounds_and_unmarked_directory(tmp_path):
    for count in (0, 101, True):
        with pytest.raises(ValueError):
            run_worker(tmp_path, "code", max_jobs=count)
    with pytest.raises(ValueError):
        run_worker(tmp_path, "code")
    assert not (tmp_path / "state.sqlite").exists()


def test_unknown_publication_is_not_replayed_by_worker(workspace, monkeypatch):
    directory, summary, store = workspace
    job = submit_new_healthcheck(store, summary)
    monkeypatch.setattr("executors.local.worker.publish_workbooks", publish_then_lose_response)
    result = run_worker(directory, "mock-release-v1", is_publish_enabled=True)
    assert result["processed"][0]["state"] == "publish_uncertain"
    assert store.get_job(job.job_id).state == "publish_uncertain"
    assert run_worker(directory, "mock-release-v1", is_publish_enabled=True)["processed"] == []


@pytest.mark.parametrize("checks", [1, 4, 8, 14])
def test_healthcheck_budget_interrupts_without_partial_results(workspace, checks):
    directory, summary, store = workspace
    job = submit_new_healthcheck(store, summary)
    before = MockSheets(directory / "workbooks").read("output_demo")
    factory = LimitedBudgetFactory(checks)
    result = run_worker(directory, "mock-release-v1", is_publish_enabled=True, budget_factory=factory)
    assert result["processed"][0]["state"] == "capacity_blocked"
    assert store.get_job(job.job_id).result_id is None
    assert MockSheets(directory / "workbooks").read("output_demo") == before
    store.resume(job.job_id)
    assert run_worker(directory, "mock-release-v1", is_publish_enabled=True)["processed"][0]["state"] == "succeeded"


def publish_then_lose_response(store, sheets, claim, outputs, revisions):
    from application.publication import publish_workbooks
    return publish_workbooks(store, sheets, claim, outputs, revisions, faults={"output_demo": "after"})


def test_actual_worker_cli_is_offline_and_code_bound(workspace):
    directory, summary, store = workspace
    submit_new_healthcheck(store, summary)
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP") if key in os.environ}
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run([sys.executable, "-m", "executors.local", "work", "--workspace", str(directory),
        "--code-sha", "mock-release-v1", "--publish-mock"], cwd=Path(__file__).resolve().parents[1],
        env=env, text=True, capture_output=True, timeout=10, check=True)
    payload = json.loads(result.stdout)
    assert payload["network_providers"] == []
    assert payload["processed"][0]["state"] == "succeeded"


def submit_new_healthcheck(store, summary):
    config = store.candidate_document(summary["pending_candidate_id"])
    old = store.get_job(summary["job_id"])
    target = replace(old.spec, versions={**old.spec.version_map, "healthcheck_config": config.snapshot_id})
    publication = store.publish_candidate(summary["pending_candidate_id"], (target,))
    return next(job for job in store.list_jobs() if job.spec.version_map.get("healthcheck_config") == publication.config_id)


def make_problem():
    member = RaidMember(MemberKey("area", "member"), "mock", 100)
    return RaidProblem((member,), (("0",) * 5,) * 3, ELEMENTS, 100, ("10",) * 5, (),
        ((member.member, FixedInputs(("", "", ""))),),
        ((member.member, tuple((e, i + 1) for i, e in enumerate(ELEMENTS))),), (("inputs", "mock"),), "code")


def tiny_budget():
    return ComputeBudget(max_checks=1)


class LimitedBudgetFactory:
    def __init__(self, checks):
        self.checks = checks

    def __call__(self):
        return ComputeBudget(max_checks=self.checks)
