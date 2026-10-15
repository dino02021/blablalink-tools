import pytest

from adapters.local_outbox import LocalOutbox
from adapters.local_retention import LocalObjectRetention
from application.report_delivery import compile_report_candidate
from application.tasks import StateConflict, TaskSpec
from domain.normalization import capture_raw_training, normalize_training
from domain.snapshots import capture_document
from executors.local.demo import raw_member, run_demo


def test_orphans_delete_only_after_fixed_confirmation_and_replay(tmp_path):
    store = LocalObjectRetention(tmp_path / "state.sqlite", clock=fixed_clock)
    try:
        first = store.put_document(document("first"))
        second = store.put_document(document("second"))
        plan = store.plan_retention(101, limit=1)
        assert len(plan.object_ids) == 1
        assert store.get_document(first) and store.get_document(second)
        with pytest.raises(StateConflict):
            store.apply_retention(plan, "wrong_confirmation")
        assert store.apply_retention(plan, plan.plan_id) == plan.object_ids
        assert store.apply_retention(plan, plan.plan_id) == plan.object_ids
        assert store._connection.execute("SELECT COUNT(*) FROM objects").fetchone()[0] == 1
    finally:
        store.close()


def test_reference_added_after_plan_blocks_entire_cleanup(tmp_path):
    store = LocalObjectRetention(tmp_path / "state.sqlite", clock=fixed_clock)
    try:
        object_id = store.put_document(document("training"))
        plan = store.plan_retention(101)
        store.submit_job("request", TaskSpec("prepare_layout", "scope", "code", {"pinned_object": object_id}))
        with pytest.raises(StateConflict):
            store.apply_retention(plan, plan.plan_id)
        assert store.get_document(object_id)
    finally:
        store.close()


def test_training_embedded_raw_reference_is_transitively_retained(tmp_path):
    store = LocalObjectRetention(tmp_path / "state.sqlite", clock=fixed_clock)
    try:
        raw = capture_raw_training("scope", "2026-10-15T00:00:00Z", "reference", (raw_member("member", 5),))
        training = normalize_training(raw, ("role_demo",), {}).training
        store.put_document(raw)
        store.put_document(training)
        orphan = store.put_document(document("orphan"))
        store.submit_job("request", TaskSpec("prepare_layout", "scope", "code", {"training": training.snapshot_id}))
        plan = store.plan_retention(101)
        assert plan.object_ids == (orphan,)
        store.apply_retention(plan, plan.plan_id)
        assert store.get_document(raw.snapshot_id) and store.get_document(training.snapshot_id)
    finally:
        store.close()


def test_prepared_report_route_and_published_results_are_roots(tmp_path):
    directory = tmp_path / "demo"
    summary = run_demo(directory)
    store = LocalObjectRetention(directory / "state.sqlite")
    outbox = LocalOutbox(directory / "state.sqlite")
    try:
        routes = capture_document("union_inputs", "union_demo", "2026-10-15T00:05:00Z", "reference_demo",
            {"routing_kind": "report_routes", "routes": [{"member": {"area_id": "area_demo", "member_id": "member_b"},
                "target_thread": "thread_mock"}]})
        store.put_document(routes)
        candidate = compile_report_candidate(store, summary["job_id"], routes.snapshot_id, "intent")
        outbox.prepare_batch(candidate)
        assert store.plan_retention(9999999999).object_ids == ()
    finally:
        outbox.close()
        store.close()


def test_cutoff_and_inventory_changes_need_fresh_plan(tmp_path):
    store = LocalObjectRetention(tmp_path / "state.sqlite", clock=fixed_clock)
    try:
        store.put_document(document("first"))
        assert store.plan_retention(100).object_ids == ()
        plan = store.plan_retention(101)
        store.put_document(document("second"))
        with pytest.raises(StateConflict):
            store.apply_retention(plan, plan.plan_id)
        assert store._connection.execute("SELECT COUNT(*) FROM objects").fetchone()[0] == 2
    finally:
        store.close()


def document(label):
    return capture_document("union_inputs", "scope", "2026-10-15T00:00:00Z", "reference", {"label": label})


def fixed_clock():
    return 100
