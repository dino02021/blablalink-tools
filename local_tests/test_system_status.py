from contextlib import closing
from dataclasses import replace
import json
import sqlite3

import pytest

from adapters.local_state import LocalStateStore
from adapters.local_raid_plans import LocalRaidPlans
from adapters.local_system_status import read_system_status
from application.system_status_view import render_system_status
from application.tasks import TaskSpec
from domain.healthcheck import ELEMENTS
from domain.raid import RaidMember
from domain.raid_planner import RaidProblem, select_rounds
from domain.raid_finalize import finalize_rounds
from domain.snapshots import MemberKey
from executors.local.demo import run_demo
from executors.local.process_guard import run_python_module


def test_status_is_read_only_and_separates_desired_from_last_publication(tmp_path):
    directory = tmp_path / "mock"
    summary = run_demo(directory)
    before = (directory / "state.sqlite").read_bytes()
    status = read_system_status(directory, ("union_demo", "empty_union"))
    assert (directory / "state.sqlite").read_bytes() == before
    scope, empty = status["scopes"]
    assert scope["is_all_desired_completed"] and scope["last_published"]["job_id"] == summary["job_id"]
    assert scope["last_published"]["is_current_target"]
    assert empty["desired"] == [] and not empty["is_all_desired_completed"]
    assert not status["global"]["is_write_blocked"]
    assert read_system_status(directory, ("union_demo", "empty_union"))["status_hash"] == status["status_hash"]
    assert len(render_system_status(status).rows) == 3


def test_one_failed_union_does_not_hide_success_or_previous_output(tmp_path):
    directory = tmp_path / "mock"
    summary = run_demo(directory)
    with closing(LocalStateStore(directory / "state.sqlite")) as store:
        original = store.get_job(summary["job_id"])
        next_spec = replace(original.spec, versions={**original.spec.version_map, "status_test": "new"})
        job = store.submit_job("new", next_spec, is_desired=True)
        lease = store.claim("worker", "mock-release-v1")
        store.defer(lease, "failed", "mock_failure")
        healthy = store.submit_job("healthy", TaskSpec("prepare_layout", "healthy_union", "code"), is_desired=True)
        lease = store.claim("worker", "code")
        store.complete_compute(lease, {"only_mock": True}, is_publication_required=False)
    status = read_system_status(directory, ("union_demo", "healthy_union"))
    failed, healthy_scope = status["scopes"]
    assert failed["has_blocked_target"] and failed["desired"][0]["job_id"] == job.job_id
    assert failed["last_published"]["job_id"] == summary["job_id"] and not failed["last_published"]["is_current_target"]
    assert healthy_scope["is_all_desired_completed"] and healthy_scope["desired"][0]["job_id"] == healthy.job_id


def test_computed_is_pending_not_published_and_global_unknown_keeps_lock_visible(tmp_path):
    directory = tmp_path / "mock"
    summary = run_demo(directory)
    with closing(LocalStateStore(directory / "state.sqlite")) as store:
        original = store.get_job(summary["job_id"])
        job = store.submit_job("new", replace(original.spec, versions={**original.spec.version_map, "status": "new"}), is_desired=True)
        lease = store.claim("worker", "mock-release-v1")
        store.complete_compute(lease, {"workbooks": {"shared_mock": {}}, "layout_revisions": {"shared_mock": "schema"}})
        computed = read_system_status(directory, ("union_demo",))
        assert computed["scopes"][0]["has_pending_target"] and not computed["scopes"][0]["is_all_desired_completed"]
        claim = store.begin_publication(job.job_id, "publisher", "mock-release-v1", ("shared_mock",))
        store.mark_publication_uncertain(claim)
    status = read_system_status(directory, ("union_demo", "other_union"))
    assert status["global"]["is_write_blocked"]
    assert {row["resource_id"] for row in status["global"]["resource_locks"]} == {"global:publisher", "sheet:shared_mock"}
    assert all(row["publication_id"] == claim.publication_id for row in status["global"]["resource_locks"])
    assert status["global"]["publication_holds"][0]["state"] == "publish_uncertain"
    assert status["scopes"][0]["last_published"]["job_id"] == summary["job_id"]


def test_corrupt_job_spec_is_isolated_to_its_scope_and_no_body_is_returned(tmp_path):
    directory = tmp_path / "mock"
    summary = run_demo(directory)
    with closing(LocalStateStore(directory / "state.sqlite")) as store:
        store._connection.execute("UPDATE jobs SET spec_json=? WHERE job_id=?", ('{"private_body":"must-not-appear"}', summary["job_id"]))
    status = read_system_status(directory, ("union_demo", "other_union"))
    assert not status["scopes"][0]["is_available"] and status["scopes"][1]["is_available"]
    assert "must-not-appear" not in json.dumps(status)
    assert render_system_status(status).rows[1][-1].style == "failed"


def test_status_works_on_restore_fenced_workspace_without_removing_fence(tmp_path):
    directory = tmp_path / "mock"
    run_demo(directory)
    fence = directory / "restore-lock.json"
    fence.write_text("{}", encoding="utf-8")
    assert read_system_status(directory, ("union_demo",))["is_restore_fenced"]
    assert fence.read_text(encoding="utf-8") == "{}"


def test_status_does_not_migrate_missing_schema_or_initialize_new_paths(tmp_path):
    with pytest.raises(ValueError, match="initialized"):
        read_system_status(tmp_path / "absent", ("union",))
    assert not (tmp_path / "absent").exists()
    directory = tmp_path / "mock"
    run_demo(directory)
    with sqlite3.connect(directory / "state.sqlite") as connection:
        connection.execute("DROP TABLE reference_heads")
    before = (directory / "state.sqlite").read_bytes()
    with pytest.raises(sqlite3.Error):
        read_system_status(directory, ("union_demo",))
    assert (directory / "state.sqlite").read_bytes() == before


def test_status_view_rejects_source_substitution_and_invalid_scope_sets(tmp_path):
    directory = tmp_path / "mock"
    run_demo(directory)
    status = read_system_status(directory, ("union_demo",))
    status["scopes"][0]["is_all_desired_completed"] = False
    with pytest.raises(ValueError, match="hash"):
        render_system_status(status)
    for scopes in ((), "abc", ("same", "same"), tuple(f"union_{i}" for i in range(101))):
        with pytest.raises(ValueError):
            read_system_status(directory, scopes)


def test_read_transaction_keeps_one_snapshot_during_concurrent_writer(tmp_path, monkeypatch):
    import adapters.local_system_status as status_module
    directory = tmp_path / "mock"
    summary = run_demo(directory)
    original_reader = status_module._global_status
    with closing(LocalStateStore(directory / "state.sqlite")) as store:
        store._connection.execute("PRAGMA journal_mode=WAL")
        original = store.get_job(summary["job_id"])
        job = store.submit_job("new", replace(original.spec, versions={**original.spec.version_map, "status": "new"}), is_desired=True)
        lease = store.claim("worker", "mock-release-v1")
        monkeypatch.setattr(status_module, "_global_status", ConcurrentWriter(original_reader, store, lease))
        snapshot = read_system_status(directory, ("union_demo",))
        assert snapshot["scopes"][0]["desired"][0]["state"] == "running"
        assert store.get_job(job.job_id).state == "failed"
        monkeypatch.setattr(status_module, "_global_status", original_reader)
        assert read_system_status(directory, ("union_demo",))["scopes"][0]["desired"][0]["state"] == "failed"


def test_stale_raid_confirmation_is_distinct_from_latest_healthcheck(tmp_path):
    directory = tmp_path / "mock"
    run_demo(directory)
    member = RaidMember(MemberKey("area", "member"), "same", 100)
    problem = RaidProblem((member,), (("0",)*5,)*3, ELEMENTS, 100, ("10",)*5, (), (), (), (("inputs", "old"),), "code")
    with closing(LocalRaidPlans(directory / "state.sqlite")) as store:
        revision = store.observe_problem("union_demo", problem, 0)
        final = finalize_rounds(problem, select_rounds(problem))
        draft = store.save_draft("union_demo", problem, final)
        confirmation = store.confirm("request", "intent", "union_demo", draft, "actor", revision, None)
        store.observe_problem("union_demo", replace(problem, input_versions=(("inputs", "new"),)), revision)
    status = read_system_status(directory, ("union_demo",))
    assert status["scopes"][0]["is_all_desired_completed"]
    assert status["scopes"][0]["raid_confirmation"] == {"confirmation_id": confirmation, "draft_plan_id": draft,
        "problem_id": problem.problem_id, "is_stale": True}
    view = render_system_status(status)
    assert view.rows[1][9].style == "unknown"


class ConcurrentWriter:
    def __init__(self, reader, store, lease):
        self.reader, self.store, self.lease = reader, store, lease

    def __call__(self, connection, scopes):
        result = self.reader(connection, scopes)
        self.store.defer(self.lease, "failed", "concurrent_failure")
        return result


def test_status_cli_returns_only_selected_mock_scopes_under_process_limits(tmp_path):
    directory = tmp_path / "mock"
    run_demo(directory)
    before = (directory / "state.sqlite").read_bytes()
    result = run_python_module("executors.local.status", ("--workspace", str(directory), "--scope", "union_demo", "--scope", "empty"), seconds=5)
    assert result["outcome"] == "completed"
    payload = json.loads(result["stdout"])
    assert [scope["scope_id"] for scope in payload["status"]["scopes"]] == ["union_demo", "empty"]
    assert payload["view"]["is_generated_protected"]
    assert (directory / "state.sqlite").read_bytes() == before
