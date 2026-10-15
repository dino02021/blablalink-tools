from dataclasses import asdict, replace

import pytest

from adapters.local_raid_plans import LocalRaidPlans
from application.compute_budget import ComputeBudget, ComputeLimit
from application.raid_execution import RaidExecutionCheck, restore_selection, run_raid_job
from application.raid_workbook import restore_raid_workbook
from application.tasks import StateConflict, TaskSpec
from domain.execution import check_execution, execution_checks
from domain.healthcheck import ELEMENTS
from domain.raid import RaidMember
from domain.raid_planner import RaidProblem, select_rounds
from domain.raid_finalize import finalize_rounds
from domain.snapshots import MemberKey


@pytest.fixture
def execution(tmp_path):
    store = LocalRaidPlans(tmp_path / "state.sqlite")
    problem = _problem()
    spec = TaskSpec("generate_raid_draft", "union_demo", problem.code_sha, {"problem": problem.problem_id}, family="raid")
    job = store.submit_job("request", spec, is_desired=True)
    lease = store.claim("worker", problem.code_sha)
    yield store, job, lease, problem
    store.close()


def test_pipeline_creates_draft_but_never_confirms_or_publishes(execution):
    store, job, lease, problem = execution
    result = run_raid_job(store, lease, problem, ComputeBudget())
    assert result.state == "succeeded"
    payload = store.get_result(result.result_id)
    assert payload["problem_id"] == problem.problem_id
    workbook = restore_raid_workbook(payload["workbook_view"])
    assert workbook.view_id == payload["workbook_view_id"]
    assert workbook.problem_id == payload["problem_id"] and workbook.draft_plan_id == payload["draft_plan_id"]
    assert store.draft("union_demo", payload["draft_plan_id"])
    assert store.confirmed_head("union_demo") is None
    assert store.load_checkpoint(job.job_id)["stage"] == "finalized"


def test_operation_limit_preserves_checkpoint_and_local_retry_finishes(execution):
    store, job, lease, problem = execution
    result = run_raid_job(store, lease, problem, ComputeBudget(max_checks=1))
    assert result.state == "capacity_blocked" and result.result_id is None
    assert result.error_code == "compute_operation_limit"
    assert store.load_checkpoint(job.job_id)["stage"] == "start"
    store.reroute(job.job_id, "local", problem.code_sha)
    replacement = store.claim("replacement", problem.code_sha)
    assert run_raid_job(store, replacement, problem, ComputeBudget()).state == "succeeded"


def test_restart_resumes_completed_selection_without_recomputing_it(execution, tmp_path, monkeypatch):
    store, job, lease, problem = execution
    selected = select_rounds(problem)
    monkeypatch.setattr("application.raid_execution.finalize_rounds", _force_deadline)
    interrupted = run_raid_job(store, lease, problem, ComputeBudget())
    assert interrupted.state == "capacity_blocked"
    assert store.load_checkpoint(job.job_id)["stage"] == "selected"
    monkeypatch.setattr("application.raid_execution.finalize_rounds", finalize_rounds)
    store.close()
    reopened = LocalRaidPlans(tmp_path / "state.sqlite")
    monkeypatch.setattr("application.raid_execution.select_rounds", _unexpected_recompute)
    try:
        reopened.reroute(job.job_id, "local", problem.code_sha)
        replacement = reopened.claim("replacement", problem.code_sha)
        result = run_raid_job(reopened, replacement, problem, ComputeBudget())
        assert result.state == "succeeded"
        checkpoint = reopened.load_checkpoint(job.job_id)
        assert restore_selection(checkpoint["selection"]) == selected
    finally:
        reopened.close()


def test_checkpoint_for_other_input_or_code_is_rejected(execution):
    store, job, lease, problem = execution
    store.save_checkpoint(lease, {"schema_version": 1, "problem_id": "another", "code_sha": problem.code_sha, "stage": "start"})
    with pytest.raises(StateConflict, match="checkpoint"):
        run_raid_job(store, lease, problem, ComputeBudget())
    with pytest.raises(StateConflict, match="inputs"):
        run_raid_job(store, lease, replace(problem, code_sha="another_code"), ComputeBudget())
    assert store.get_job(job.job_id).result_id is None


def test_deadline_reserve_stops_before_platform_deadline():
    clock = Clock()
    budget = ComputeBudget(seconds=240, reserve_seconds=10, clock=clock)
    clock.now = 229
    budget.check()
    clock.now = 230
    with pytest.raises(ComputeLimit, match="deadline"):
        budget.check()


def test_clock_deadline_interrupts_inside_actual_computation(execution):
    store, job, lease, problem = execution
    clock = AdvancingClock()
    budget = ComputeBudget(seconds=20, reserve_seconds=5, clock=clock)
    result = run_raid_job(store, lease, problem, budget)
    assert result.state == "capacity_blocked" and result.error_code == "compute_deadline"
    assert result.result_id is None
    assert budget.last_checked_at == 15
    assert budget.checks > 1


def test_unsupported_checkpoint_algorithm_cannot_resume():
    problem = _problem()
    payload = asdict(select_rounds(problem))
    payload["algorithm_version"] = "future_incompatible"
    with pytest.raises(StateConflict):
        restore_selection(payload)


@pytest.mark.parametrize("used,code", [(100, "compute_memory_limit"), (None, "compute_memory_unknown"), (True, "compute_memory_unknown")])
def test_memory_budget_requires_trustworthy_reading(used, code):
    budget = ComputeBudget(memory_limit_bytes=100, memory_reader=lambda: used)
    with pytest.raises(ComputeLimit, match=code):
        budget.check()


def test_memory_exit_does_not_store_partial_result(execution):
    store, job, lease, problem = execution
    result = run_raid_job(store, lease, problem, ComputeBudget(memory_limit_bytes=100, memory_reader=lambda: 101))
    assert result.state == "capacity_blocked"
    assert result.error_code == "compute_memory_limit"
    assert result.result_id is None
    assert store.confirmed_head("union_demo") is None


def test_execution_hook_is_reset_after_interruption():
    budget = ComputeBudget(max_checks=1)
    with pytest.raises(ComputeLimit):
        with execution_checks(budget.check):
            check_execution()
            check_execution()
    check_execution()  # prior failed invocation cannot poison the next computation


def test_execution_check_heartbeats_at_bounded_interval():
    clock = Clock()
    budget = ComputeBudget(clock=clock)
    recorder = HeartbeatRecorder()
    checker = RaidExecutionCheck(recorder, "mock_lease", budget)
    checker()
    clock.now = 14
    checker()
    assert recorder.calls == 1
    clock.now = 15
    checker()
    assert recorder.calls == 2


def _unexpected_recompute(*args, **kwargs):
    raise AssertionError("completed selection must not be recomputed after restart")


def _force_deadline(*args, **kwargs):
    raise ComputeLimit("compute_deadline")


def _problem():
    member = RaidMember(MemberKey("area", "member"), "member_demo", 100)
    return RaidProblem((member,), (("0",) * 5,) * 3, ELEMENTS, 100, ("10",) * 5, (), (),
        ((member.member, tuple((e, i + 1) for i, e in enumerate(ELEMENTS))),), (("inputs", "demo"),), "code_demo")


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


class AdvancingClock:
    now = 0

    def __call__(self):
        value = self.now
        self.now += 1
        return value


class HeartbeatRecorder:
    calls = 0

    def heartbeat(self, lease):
        assert lease == "mock_lease"
        self.calls += 1
