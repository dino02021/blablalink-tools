"""Durable local coordination with temporary databases and synthetic identities."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
import sqlite3
from threading import Barrier

import pytest

from adapters.local_state import LocalStateStore
from application.tasks import BudgetExceeded, BudgetNeed, LeaseLost, ResourceBusy, StateConflict, TaskSpec
from domain.healthcheck import MetricRule, RoleRule
from domain.snapshots import RoleLabel, capture_config


@dataclass
class MockClock:
    value: float = 1000

    def __call__(self):
        return self.value


@pytest.fixture
def state(tmp_path):
    clock = MockClock()
    path = tmp_path / "local_state.sqlite3"
    store = LocalStateStore(path, clock)
    yield store, clock, path
    store.close()


def test_durable_request_and_immutable_object_roundtrip(state):
    store, clock, path = state
    document = _config("first")
    assert store.put_document(document) == document.snapshot_id
    spec = _task()
    first = store.submit_job("request_demo", spec, is_desired=True)
    assert store.submit_job("request_demo", spec, is_desired=True) == first
    assert store.submit_job("another_request", replace(spec, priority=9), is_desired=True).job_id == first.job_id
    assert len(store.events(first.job_id)) == 1
    with pytest.raises(StateConflict):
        store.submit_job("request_demo", replace(spec, code_sha="other_code"), is_desired=True)
    with pytest.raises(StateConflict):
        store.submit_job("request_demo", spec)
    reopened = LocalStateStore(path, clock)
    try:
        assert reopened.get_job(first.job_id).state == "queued"
        assert reopened.get_document(document.snapshot_id) == document
    finally:
        reopened.close()


def test_persisted_payload_integrity_is_checked(state):
    store, _, path = state
    document = _config("first")
    store.put_document(document)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE objects SET serialized='{}' WHERE object_id=?", (document.snapshot_id,))
    with pytest.raises(StateConflict, match="integrity"):
        store.get_document(document.snapshot_id)


def test_config_head_and_fanout_rollback_as_one_transaction(state):
    store, _, _ = state
    candidate = store.create_candidate(_config("first"), None)
    invalid = replace(_task("union_b"), dependencies=("missing_parent",))
    with pytest.raises(KeyError):
        store.publish_candidate(candidate, (_task("union_a"), invalid))
    assert store.config_head("healthcheck", "common") is None
    assert store.list_jobs() == ()
    published = store.publish_candidate(candidate, (_task("union_a"), _task("union_b")))
    assert len(published.job_ids) == 2
    assert store.config_head("healthcheck", "common") == published.config_id
    assert all(store.get_job(job_id).spec.version_map["healthcheck_config"] == published.config_id for job_id in published.job_ids)
    assert store.publish_candidate(candidate, (_task("union_b"), _task("union_a"))) == published
    assert store.create_candidate(_config("first"), None) == candidate
    with pytest.raises(StateConflict, match="target set"):
        store.publish_candidate(candidate, (_task("union_a"),))


def test_stale_candidate_and_unpublished_job_config_are_rejected(state):
    store, _, _ = state
    stale = store.create_candidate(_config("stale"), None)
    other = store.create_candidate(_config("winner"), None)
    store.publish_candidate(other, ())
    with pytest.raises(StateConflict, match="changed after"):
        store.publish_candidate(stale, ())
    with pytest.raises(StateConflict, match="not a published"):
        store.submit_job("bad_request", replace(_task(), versions={"healthcheck_config": "unknown"}))


def test_config_publication_does_not_send_discord(state):
    store, _, _ = state
    candidate = store.create_candidate(_config("first"), None)
    send = TaskSpec("send_reports", "union_demo", "code_demo", intent_id="confirmed_send_demo")
    with pytest.raises(ValueError, match="recomputation"):
        store.publish_candidate(candidate, (send,))
    assert store.config_head("healthcheck", "common") is None


def test_expired_worker_is_fenced_and_reclaimed_durably(state):
    store, clock, _ = state
    job = store.submit_job("request", _task())
    first = store.claim("worker_old", "code_demo", lease_seconds=10)
    clock.value += 10
    with pytest.raises(LeaseLost):
        store.complete_compute(first, {"value": 1})
    second = store.claim("worker_new", "code_demo", lease_seconds=10)
    assert second.generation == first.generation + 1
    assert store.get_job(job.job_id).attempt == 2
    with pytest.raises(LeaseLost):
        store.heartbeat(first)
    completed = store.complete_compute(second, {"value": 2})
    assert completed.state == "computed"
    assert store.get_result(completed.result_id) == {"value": 2}


def test_heartbeat_extends_current_lease(state):
    store, clock, _ = state
    job = store.submit_job("request", _task())
    lease = store.claim("worker", "code_demo", lease_seconds=10)
    clock.value += 9
    store.heartbeat(lease, lease_seconds=10)
    clock.value += 2
    assert store.claim("other_worker", "code_demo") is None
    assert store.complete_compute(lease, {}, is_publication_required=False).state == "succeeded"
    assert store.get_job(job.job_id).generation == 1


def test_retry_deadline_and_attempt_limit_cannot_be_bypassed_by_resubmission(state):
    store, clock, _ = state
    spec = replace(_task(), max_attempts=2)
    job = store.submit_job("request", spec)
    first = store.claim("worker", "code_demo")
    store.defer(first, "retry_wait", "temporary_network", delay_seconds=10)
    assert store.claim("worker", "code_demo") is None
    clock.value += 10
    second = store.claim("worker", "code_demo")
    assert store.defer(second, "retry_wait", "temporary_network").state == "failed"
    assert store.submit_job("new_request", spec).job_id == job.job_id
    assert store.claim("worker", "code_demo") is None


def test_expiry_on_last_attempt_fails_without_publisher_authority(state):
    store, clock, _ = state
    job = store.submit_job("request", replace(_task(), max_attempts=1))
    store.claim("worker", "code_demo", lease_seconds=1)
    clock.value += 1
    store.recover()
    assert store.get_job(job.job_id).state == "failed"


def test_dependencies_wait_for_actual_success_and_propagate_failure(state):
    store, _, _ = state
    parent = store.submit_job("parent_request", replace(_task(), family="parent"))
    child = store.submit_job("child_request", replace(_task(), family="child", dependencies=(parent.job_id,)))
    grandchild = store.submit_job("grandchild_request", replace(_task(), family="grandchild", dependencies=(child.job_id,)))
    lease = store.claim("worker", "code_demo")
    assert lease.job_id == parent.job_id
    store.defer(lease, "failed", "invalid_source")
    store.recover()
    assert store.get_job(child.job_id).error_code == "dependency_failed"
    assert store.get_job(grandchild.job_id).state == "failed"
    assert store.claim("worker", "code_demo") is None


def test_dependency_success_unlocks_child(state):
    store, _, _ = state
    parent = store.submit_job("parent_request", replace(_task(), family="parent"))
    child = store.submit_job("child_request", replace(_task(), family="child", dependencies=(parent.job_id,)))
    store.complete_compute(store.claim("worker", "code_demo"), {"artifact": "demo"}, is_publication_required=False)
    assert store.claim("worker", "code_demo").job_id == child.job_id


def test_current_version_supersedes_old_computation_before_publish(state):
    store, _, _ = state
    original = store.submit_job("original", _task(), is_desired=True)
    old_lease = store.claim("worker", "code_demo")
    newest = store.submit_job("newest", replace(_task(), versions={"data": "new_data"}), is_desired=True)
    assert store.complete_compute(old_lease, {}).state == "superseded"
    with pytest.raises(StateConflict):
        store.begin_publication(original.job_id, "publisher", "code_demo", ("sheet_demo",))
    assert store.claim("worker", "code_demo").job_id == newest.job_id


def test_unknown_publication_keeps_locks_across_time_and_restart(state):
    store, clock, path = state
    first = _computed(store, "union_a")
    second = _computed(store, "union_b")
    claim = store.begin_publication(first.job_id, "publisher_a", "code_demo", ("shared_sheet", "sheet_a"))
    assert store.begin_publication(first.job_id, "publisher_a", "code_demo", ("sheet_a", "shared_sheet")) == claim
    store.record_resource_observation(claim, "sheet_a", claim.commit_id)
    store.mark_publication_uncertain(claim)
    assert [(item["resource_id"], item["state"]) for item in store.publication_progress(claim.publication_id)] == [
        ("shared_sheet", "unknown"), ("sheet_a", "verified"),
    ]
    clock.value += 100000
    reopened = LocalStateStore(path, clock)
    try:
        reopened.recover()
        assert reopened.get_job(first.job_id).state == "publish_uncertain"
        with pytest.raises(ResourceBusy):
            reopened.begin_publication(second.job_id, "publisher_b", "code_demo", ("shared_sheet",))
        with pytest.raises(StateConflict, match="termination"):
            reopened.recover_publication(claim.publication_id, "new_publisher", {"sheet_a": None, "shared_sheet": None}, False)
        observations = {"sheet_a": claim.commit_id, "shared_sheet": None}
        new_claim = reopened.recover_publication(claim.publication_id, "new_publisher", observations, True)
        assert new_claim.commit_id == claim.commit_id and new_claim.generation == claim.generation + 1
        with pytest.raises(LeaseLost):
            reopened.complete_publication(claim, {resource: claim.commit_id for resource in claim.resources})
        complete = reopened.complete_publication(new_claim, {resource: new_claim.commit_id for resource in new_claim.resources})
        assert complete.state == "succeeded"
        assert all(item["state"] == "verified" for item in reopened.publication_progress(new_claim.publication_id))
        assert reopened.begin_publication(second.job_id, "publisher_b", "code_demo", ("shared_sheet",))
    finally:
        reopened.close()


def test_readback_must_cover_all_resources_and_completion_is_idempotent(state):
    store, _, _ = state
    job = _computed(store)
    claim = store.begin_publication(job.job_id, "publisher", "code_demo", ("sheet_a", "sheet_b"))
    with pytest.raises(StateConflict):
        store.complete_publication(claim, {"sheet_a": claim.commit_id})
    with pytest.raises(StateConflict):
        store.complete_publication(claim, {"sheet_a": claim.commit_id, "sheet_b": "wrong"})
    with pytest.raises(StateConflict):
        store.cancel(job.job_id)
    observations = {resource: claim.commit_id for resource in claim.resources}
    assert store.complete_publication(claim, observations).state == "succeeded"
    count = len(store.events(job.job_id))
    assert store.complete_publication(claim, observations).state == "succeeded"
    assert len(store.events(job.job_id)) == count


def test_all_applied_unknown_publication_can_be_reconciled_without_rewrite(state):
    store, _, _ = state
    job = _computed(store)
    claim = store.begin_publication(job.job_id, "publisher", "code_demo", ("sheet_demo",))
    store.mark_publication_uncertain(claim)
    assert store.recover_publication(claim.publication_id, "reconciler", {"sheet_demo": claim.commit_id}, True) is None
    assert store.get_job(job.job_id).state == "succeeded"


def test_promotion_waits_for_inflight_publisher_and_rolls_back_request(state):
    store, _, _ = state
    job = _computed(store)
    claim = store.begin_publication(job.job_id, "publisher", "code_demo", ("sheet_demo",))
    changed = replace(_task(), versions={"data": "new_data"})
    with pytest.raises(ResourceBusy):
        store.submit_job("later_request", changed, is_desired=True)
    candidate = store.create_candidate(_config("first"), None)
    with pytest.raises(ResourceBusy):
        store.publish_candidate(candidate, ())
    assert store.config_head("healthcheck", "common") is None
    store.complete_publication(claim, {"sheet_demo": claim.commit_id})
    assert store.submit_job("later_request", changed, is_desired=True).state == "queued"


def test_rollback_reuses_result_but_requires_a_new_publication(state):
    store, _, _ = state
    first_config = _config("first")
    first = store.publish_candidate(store.create_candidate(first_config, None), (_task(),))
    first_job = store.get_job(first.job_ids[0])
    initial_result = store.complete_compute(store.claim("worker", "code_demo"), {"value": "first"})
    claim = store.begin_publication(first_job.job_id, "publisher", "code_demo", ("sheet_demo",))
    store.complete_publication(claim, {"sheet_demo": claim.commit_id})
    second_config = _config("second")
    second = store.publish_candidate(store.create_candidate(second_config, first.config_id), (_task(),))
    store.complete_compute(store.claim("worker", "code_demo"), {"value": "second"})
    claim = store.begin_publication(second.job_ids[0], "publisher", "code_demo", ("sheet_demo",))
    store.complete_publication(claim, {"sheet_demo": claim.commit_id})
    rollback = store.publish_candidate(store.create_candidate(first_config, second.config_id), (_task(),))
    assert rollback.config_id == first.config_id
    assert rollback.job_ids == first.job_ids
    restored = store.get_job(first_job.job_id)
    assert restored.state == "computed" and restored.result_id == initial_result.result_id
    assert store.get_result(restored.result_id) == {"value": "first"}
    assert store.claim("worker", "code_demo") is None
    assert store.begin_publication(first_job.job_id, "publisher", "code_demo", ("sheet_demo",))


def test_unregistered_scope_still_checks_published_configuration_head(state):
    store, _, _ = state
    first = store.publish_candidate(store.create_candidate(_config("first"), None), ())
    job = store.submit_job("old_config_task", replace(_task(), versions={"healthcheck_config": first.config_id}))
    store.publish_candidate(store.create_candidate(_config("second"), first.config_id), ())
    assert store.claim("worker", "code_demo") is None
    assert store.get_job(job.job_id).state == "superseded"


def test_cloud_budget_reservation_is_atomic_and_blocked_work_can_reroute_local(state):
    store, _, _ = state
    spec = replace(_task(), executor_type="vercel")
    job = store.submit_job("cloud_request", spec)
    with pytest.raises(ValueError, match="both CPU"):
        store.dispatch(job.job_id, "scheduler", BudgetNeed("vercel_cpu_ms", "period_demo", 5))
    store.set_budget("vercel_cpu_ms", "period_demo", 10)
    store.set_budget("vercel_memory_mb_seconds", "period_demo", 0)
    needs = (BudgetNeed("vercel_cpu_ms", "period_demo", 5), BudgetNeed("vercel_memory_mb_seconds", "period_demo", 5))
    assert store.dispatch(job.job_id, "scheduler", needs) is None
    assert store.get_job(job.job_id).state == "capacity_blocked"
    assert store.budget_status("vercel_cpu_ms", "period_demo")["reserved_units"] == 0
    assert store.reservations(job.job_id) == ()
    store.reroute(job.job_id, "local", "code_demo")
    assert store.claim("local_worker", "code_demo").job_id == job.job_id


def test_cloud_claim_holds_budgets_until_actual_usage_is_settled(state):
    store, clock, _ = state
    job = store.submit_job("actions_request", replace(_task(), executor_type="actions"))
    store.set_budget("actions_minutes", "period_demo", 20)
    need = BudgetNeed("actions_minutes", "period_demo", 20)
    dispatch = store.dispatch(job.job_id, "scheduler", need)
    assert store.get_job(job.job_id).state == "dispatched"
    assert store.budget_status("actions_minutes", "period_demo")["reserved_units"] == 20
    assert store.dispatch(job.job_id, "scheduler", need) == dispatch
    lease = store.claim("actions_worker", "code_demo", "actions", lease_seconds=5,
                        dispatch_id=dispatch.dispatch_id, executor_run_id="run_demo")
    assert lease
    clock.value += 5
    store.recover()
    assert store.budget_status("actions_minutes", "period_demo")["reserved_units"] == 20
    assert store.dispatch(job.job_id, "scheduler", need) is None
    reservation = store.reservations(job.job_id)[0]
    store.settle_budget(reservation["reservation_id"], 8)
    store.settle_budget(reservation["reservation_id"], 8)
    with pytest.raises(StateConflict):
        store.settle_budget(reservation["reservation_id"], 9)
    assert store.budget_status("actions_minutes", "period_demo")["used_units"] == 8


def test_budget_replay_and_overrun_remain_visible(state):
    store, _, _ = state
    store.set_budget("actions_minutes", "period_demo", 10)
    need = BudgetNeed("actions_minutes", "period_demo", 5)
    first = store.reserve_budget("reservation_demo", need)
    assert store.reserve_budget("reservation_demo", need) == first
    with pytest.raises(StateConflict):
        store.reserve_budget("reservation_demo", BudgetNeed("actions_minutes", "period_demo", 6))
    store.settle_budget("reservation_demo", 12)
    assert store.budget_status("actions_minutes", "period_demo")["used_units"] == 12
    with pytest.raises(BudgetExceeded):
        store.reserve_budget("another_reservation", BudgetNeed("actions_minutes", "period_demo", 1))


def test_schedule_slot_is_durable_unique_and_not_a_forever_cached_refresh(state):
    store, _, _ = state
    spec = TaskSpec("refresh_raid_history", "union_demo", "code_demo", family="history", intent_id="schedule_template")
    first = store.schedule_slot("schedule_demo", 1000, spec)
    assert store.schedule_slot("schedule_demo", 1000, spec) == first
    second = store.schedule_slot("schedule_demo", 1060, spec)
    assert first.job_id != second.job_id
    with pytest.raises(StateConflict):
        store.schedule_slot("schedule_demo", 1000, replace(spec, code_sha="other_code"))


def test_wrong_executor_code_and_cancellation_cannot_complete_work(state):
    store, _, _ = state
    job = store.submit_job("request", _task())
    assert store.claim("worker", "wrong_code") is None
    lease = store.claim("worker", "code_demo")
    with pytest.raises(StateConflict):
        store.reroute(job.job_id, "actions", "code_demo")
    assert store.cancel(job.job_id).state == "cancelled"
    with pytest.raises(LeaseLost):
        store.complete_compute(lease, {})


def test_sql_special_characters_are_inert_task_values(state):
    store, _, _ = state
    scope = "union_demo'); DROP TABLE jobs;--"
    job = store.submit_job("request_demo", _task(scope))
    lease = store.claim("worker'; DELETE FROM jobs;--", "code_demo")
    assert store.get_job(job.job_id).spec.scope_id == scope
    assert store.complete_compute(lease, {}, is_publication_required=False).state == "succeeded"
    assert len(store.list_jobs()) == 1


def test_waiting_for_new_data_cannot_resume_the_same_immutable_inputs(state):
    store, _, _ = state
    job = store.submit_job("request_demo", _task(), is_desired=True)
    lease = store.claim("worker", "code_demo")
    store.defer(lease, "waiting_for_data", "incomplete_snapshot")
    with pytest.raises(StateConflict, match="new immutable"):
        store.resume(job.job_id)
    fresh = store.submit_job("fresh_request", replace(_task(), versions={"data": "complete_data"}), is_desired=True)
    assert store.claim("worker", "code_demo").job_id == fresh.job_id


def test_one_union_failure_does_not_fail_other_fanout_targets(state):
    store, _, _ = state
    published = store.publish_candidate(store.create_candidate(_config("first"), None), (_task("union_a"), _task("union_b")))
    first = store.claim("worker", "code_demo")
    store.defer(first, "failed", "incomplete_source")
    second = store.claim("worker", "code_demo")
    assert second.job_id != first.job_id
    assert store.complete_compute(second, {}, is_publication_required=False).state == "succeeded"
    assert sorted(store.get_job(job_id).state for job_id in published.job_ids) == ["failed", "succeeded"]

def test_checkpoint_survives_restart_and_stale_writer_cannot_replace_it(state):
    store, clock, path = state
    job = store.submit_job("request", _task())
    first = store.claim("first", "code_demo", lease_seconds=5)
    checkpoint_id = store.save_checkpoint(first, {"stage": "report_ready", "cursor": 2})
    clock.value += 5
    reopened = LocalStateStore(path, clock)
    try:
        second = reopened.claim("second", "code_demo")
        assert second.job_id == job.job_id
        assert reopened.load_checkpoint(job.job_id) == {"stage": "report_ready", "cursor": 2}
        with pytest.raises(LeaseLost):
            store.save_checkpoint(first, {"stage": "stale"})
        assert reopened.save_checkpoint(second, {"stage": "finished"}) != checkpoint_id
    finally:
        reopened.close()


def test_pending_dispatch_expiry_is_bounded_and_never_refunds_unknown_usage(state):
    store, clock, _ = state
    job = store.submit_job("request", replace(_task(), executor_type="actions", max_attempts=1))
    store.set_budget("actions_minutes", "period_demo", 20)
    ticket = store.dispatch(job.job_id, "scheduler", BudgetNeed("actions_minutes", "period_demo", 20), timeout_seconds=5)
    assert store.get_job(job.job_id).state == "dispatched"
    assert len(store.dispatch_reservations(ticket.dispatch_id)) == 1
    assert store.get_job(job.job_id).attempt == 0
    clock.value += 5
    store.recover()
    assert store.get_job(job.job_id).state == "failed"
    assert store.get_job(job.job_id).error_code == "dispatch_expired"
    assert store.budget_status("actions_minutes", "period_demo")["reserved_units"] == 20
    with pytest.raises(LeaseLost):
        store.claim("late_worker", "code_demo", "actions", dispatch_id=ticket.dispatch_id, executor_run_id="late_run")


def test_hosted_claim_requires_reserved_dispatch_and_replay_is_run_scoped(state):
    store, _, _ = state
    job = store.submit_job("request", replace(_task(), executor_type="actions"))
    with pytest.raises(ValueError, match="pre-budgeted"):
        store.claim("worker", "code_demo", "actions")
    store.set_budget("actions_minutes", "period_demo", 20)
    ticket = store.dispatch(job.job_id, "scheduler", BudgetNeed("actions_minutes", "period_demo", 20))
    lease = store.claim("worker", "code_demo", "actions", dispatch_id=ticket.dispatch_id, executor_run_id="run_demo")
    assert store.claim("worker", "code_demo", "actions", dispatch_id=ticket.dispatch_id, executor_run_id="run_demo") == lease
    assert store.get_job(job.job_id).attempt == 1
    with pytest.raises(LeaseLost):
        store.claim("worker", "code_demo", "actions", dispatch_id=ticket.dispatch_id, executor_run_id="other_run")


def test_old_dispatch_cannot_hijack_new_generation_with_reused_worker_name(state):
    store, clock, _ = state
    job = store.submit_job("request", replace(_task(), executor_type="actions"))
    store.set_budget("actions_minutes", "period_demo", 100)
    need = BudgetNeed("actions_minutes", "period_demo", 20)
    first = store.dispatch(job.job_id, "scheduler", need)
    lease = store.claim("same_worker", "code_demo", "actions", lease_seconds=5,
                        dispatch_id=first.dispatch_id, executor_run_id="old_run")
    clock.value += 5
    store.recover()
    second = store.dispatch(job.job_id, "scheduler", need)
    fresh = store.claim("same_worker", "code_demo", "actions", dispatch_id=second.dispatch_id, executor_run_id="new_run")
    assert fresh.generation == lease.generation + 1
    with pytest.raises(LeaseLost):
        store.claim("same_worker", "code_demo", "actions", dispatch_id=first.dispatch_id, executor_run_id="old_run")


def test_outdated_hosted_dispatch_aborts_before_computing(state):
    store, _, _ = state
    spec = replace(_task(), executor_type="actions")
    job = store.submit_job("request", spec, is_desired=True)
    store.set_budget("actions_minutes", "period_demo", 20)
    ticket = store.dispatch(job.job_id, "scheduler", BudgetNeed("actions_minutes", "period_demo", 20))
    store.submit_job("newer_request", replace(spec, versions={"data": "newer"}), is_desired=True)
    assert store.claim("worker", "code_demo", "actions", dispatch_id=ticket.dispatch_id, executor_run_id="old_run") is None
    assert store.get_job(job.job_id).state == "superseded"


def test_migration_checksum_and_publication_lock_corruption_fail_closed(state):
    store, _, path = state
    job = _computed(store)
    claim = store.begin_publication(job.job_id, "publisher", "code_demo", ("sheet_demo",))
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM resource_locks WHERE resource_id='global:publisher'")
    with pytest.raises(StateConflict, match="integrity"):
        store.complete_publication(claim, {"sheet_demo": claim.commit_id})
    with pytest.raises(ResourceBusy):
        store.submit_job("newer", replace(_task(), versions={"data": "newer"}), is_desired=True)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE schema_migrations SET checksum='corrupted'")
    with pytest.raises(StateConflict, match="checksum"):
        LocalStateStore(path, MockClock())


def test_two_connections_cannot_begin_publication_together(state):
    store, _, path = state
    jobs = (_computed(store, "union_a"), _computed(store, "union_b"))
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_publish_output_in_connection, path, barrier, job.job_id) for job in jobs]
        outcomes = [future.result(timeout=8) for future in futures]
    assert sorted(outcomes) == ["busy", "claimed"]
    assert sorted(store.get_job(job.job_id).state for job in jobs) == ["computed", "publishing"]

def test_two_connections_cannot_claim_same_job(state):
    store, _, path = state
    job = store.submit_job("request", _task())
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_claim_in_connection, path, barrier, f"worker_{number}") for number in range(2)]
        leases = [future.result(timeout=8) for future in futures]
    assert len([lease for lease in leases if lease is not None]) == 1
    assert store.get_job(job.job_id).attempt == 1


def test_config_cas_and_budget_reservation_are_serialized_between_connections(state):
    store, _, path = state
    candidates = [store.create_candidate(_config(label), None) for label in ("first", "second")]
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_publish_in_connection, path, barrier, candidate) for candidate in candidates]
        outcomes = [future.result(timeout=8) for future in futures]
    assert sorted(outcomes) == ["conflict", "published"]
    store.set_budget("actions_minutes", "period_demo", 1)
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_reserve_in_connection, path, barrier, f"reserve_{number}") for number in range(2)]
        outcomes = [future.result(timeout=8) for future in futures]
    assert sorted(outcomes) == ["exhausted", "reserved"]
    assert store.budget_status("actions_minutes", "period_demo")["reserved_units"] == 1


@pytest.mark.parametrize("field,value", [
    ("priority", True), ("max_attempts", 0), ("executor_type", "paid_runner"), ("scope_id", " "),
    ("schema_version", True), ("schema_version", 2),
])
def test_invalid_task_contracts(field, value):
    data = {"command": "recompute_healthcheck", "scope_id": "union_demo", "code_sha": "code_demo", field: value}
    with pytest.raises((ValueError, TypeError)):
        TaskSpec(**data)


def test_volatile_work_and_manual_delivery_require_explicit_intent():
    for command in ("refresh_raid_history", "send_reports", "reconcile"):
        with pytest.raises(ValueError, match="intent"):
            TaskSpec(command, "union_demo", "code_demo")


def _task(scope="union_demo"):
    return TaskSpec("recompute_healthcheck", scope, "code_demo", versions={"data": "data_demo"})


def _config(label):
    rule = RoleRule("nikke_demo", ("電擊",), "output", (MetricRule("skill_1", 5, 1),), 1)
    return capture_config("common", "2026-10-15T00:00:00Z", "reference_demo", (rule,), (RoleLabel("nikke_demo", label),))


def _computed(store, scope="union_demo"):
    job = store.submit_job("compute:" + scope, _task(scope), is_desired=True)
    lease = store.claim("worker", "code_demo")
    assert lease.job_id == job.job_id
    return store.complete_compute(lease, {"value": scope})


def _claim_in_connection(path, barrier, worker_id):
    store = LocalStateStore(path, MockClock())
    try:
        barrier.wait(timeout=5)
        return store.claim(worker_id, "code_demo")
    finally:
        store.close()


def _publish_in_connection(path, barrier, candidate):
    store = LocalStateStore(path, MockClock())
    try:
        barrier.wait(timeout=5)
        try:
            store.publish_candidate(candidate, ())
            return "published"
        except StateConflict:
            return "conflict"
    finally:
        store.close()


def _reserve_in_connection(path, barrier, reservation_id):
    store = LocalStateStore(path, MockClock())
    try:
        barrier.wait(timeout=5)
        try:
            store.reserve_budget(reservation_id, BudgetNeed("actions_minutes", "period_demo", 1))
            return "reserved"
        except BudgetExceeded:
            return "exhausted"
    finally:
        store.close()


def _publish_output_in_connection(path, barrier, job_id):
    store = LocalStateStore(path, MockClock())
    try:
        barrier.wait(timeout=5)
        try:
            store.begin_publication(job_id, "publisher:" + job_id, "code_demo", ("shared_sheet",))
            return "claimed"
        except ResourceBusy:
            return "busy"
    finally:
        store.close()
