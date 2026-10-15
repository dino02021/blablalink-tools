from contextlib import closing
import json
import sqlite3

import pytest

from adapters.local_retention import LocalObjectRetention
from adapters.local_state import LocalStateStore
from adapters.mock_collection import MockCollection
from application.collection import CollectionSourceError, run_collection_job
from application.compute_budget import ComputeBudget, ComputeLimit
from application.tasks import LeaseLost, StateConflict, TaskSpec
from domain.snapshots import MemberKey, canonical_json, capture_document, read_training

MEMBER = MemberKey("area", "member")
ROLES = ("role_a", "role_b", "role_c")
TIME = "2026-10-15T00:00:00Z"


@pytest.fixture
def collection(tmp_path):
    clock = Clock()
    path = tmp_path / "state.sqlite"
    store = LocalStateStore(path, clock)
    inputs = capture_document("union_inputs", "union", TIME, "reference",
        {"collection_kind": "training_inputs", "members": [{"area_id": "area", "member_id": "member"}],
            "role_ids": list(ROLES), "function_prefixes": {}})
    store.put_document(inputs)
    job = store.submit_job("request", TaskSpec("refresh_roster_and_training", "union", "code",
        {"collection_inputs": inputs.snapshot_id}, family="collection", intent_id="intent"))
    lease = store.claim("worker", "code")
    yield store, job, lease, clock, path
    store.close()


def page(source, rows, total, next_cursor=None):
    return {"member": {"area_id": "area", "member_id": "member"}, "scope_id": "union", "source": source,
        "reference_version": "reference", "source_revision": f"{source}-revision-secret", "total": total,
        "next_cursor": next_cursor, "rows": rows, "is_options_complete": True}


def pages():
    return {(MEMBER.key, "ownership", None): page("ownership", [{"name_code": "role_a", "grade": 0, "core": 0}], 2, "owner-cursor-secret"),
        (MEMBER.key, "ownership", "owner-cursor-secret"): page("ownership", [{"name_code": "role_b", "grade": 0, "core": 0}], 2),
        (MEMBER.key, "details", None): page("details", [{"name_code": "role_a", "skill1_lv": 5}], 2, "detail-cursor-secret"),
        (MEMBER.key, "details", "detail-cursor-secret"): page("details", [{"name_code": "role_b", "skill1_lv": 3}], 2),
        (MEMBER.key, "effects", None): page("effects", [], 0)}


def partial_run(store, lease, source="details", scripted=None):
    scripted = pages() if scripted is None else scripted
    key = (MEMBER.key, source, "detail-cursor-secret" if source == "details" else None)
    scripted[key] = TimeoutError()
    result = run_collection_job(store, lease, MockCollection(scripted), TIME, ComputeBudget())
    assert result.state == "retry_wait"
    return store.load_checkpoint(lease.job_id)


def retry(store, clock):
    clock.value += 10
    return store.claim("replacement", "code")


def test_validated_pages_are_durable_and_secrets_never_enter_objects(collection):
    store, job, lease, _, _ = collection
    scripted = pages()
    scripted[(MEMBER.key, "ownership", None)]["rows"][0]["private_key"] = "never-capture"
    checkpoint = partial_run(store, lease, scripted=scripted)
    owner = checkpoint["sources"][canonical_json(["area", "member", "ownership"])]
    details = checkpoint["sources"][canonical_json(["area", "member", "details"])]
    assert owner["status"] == "complete" and len(owner["page_ids"]) == 2
    assert details["status"] == "partial" and len(details["page_ids"]) == 1
    assert checkpoint["last_observation"]["raw_snapshot_id"]
    assert store.get_document(owner["page_ids"][0]).payload["rows"] == [{"name_code": "role_a", "grade": 0, "core": 0}]
    serialized = "\n".join(row[0] for row in store._connection.execute("SELECT serialized FROM objects"))
    assert "cursor-secret" not in serialized and "revision-secret" not in serialized and "never-capture" not in serialized
    assert store.get_job(job.job_id).result_id is None


def test_restart_reuses_complete_source_only_after_fresh_stable_revision_head(collection):
    store, job, lease, clock, path = collection
    partial_run(store, lease)
    store.close()
    with closing(LocalStateStore(path, clock)) as restarted:
        replacement = retry(restarted, clock)
        scripted = pages()
        scripted.pop((MEMBER.key, "ownership", "owner-cursor-secret"))
        port = MockCollection(scripted, is_stable_snapshot_revision=True)
        result = run_collection_job(restarted, replacement, port, "2026-10-15T00:01:00Z", ComputeBudget())
        assert result.state == "succeeded"
        assert len([call for call in port.calls if call[1] == "ownership"]) == 1
        sealed = restarted.get_result(result.result_id)
        raw = restarted.get_document(sealed["raw_snapshot_id"])
        proof = raw.payload["collection_proof"][0]["sources"]["ownership"]
        assert len(proof["pages"]) == 2 and proof["cache_validation"]["protocol"] == "stable_revision_head_v1"
        records = read_training(restarted.get_document(sealed["training_snapshot_id"]))
        assert [row.ownership_status for row in records] == ["owned", "owned", "not_owned"]
        assert sealed["collection_checkpoint_id"]


def test_without_revision_promise_all_pages_are_refetched(collection):
    store, _, lease, clock, _ = collection
    partial_run(store, lease)
    port = MockCollection(pages())
    result = run_collection_job(store, retry(store, clock), port, TIME, ComputeBudget())
    assert result.state == "succeeded"
    assert len([call for call in port.calls if call[1] == "ownership"]) == 2
    raw = store.get_document(store.get_result(result.result_id)["raw_snapshot_id"])
    assert "cache_validation" not in raw.payload["collection_proof"][0]["sources"]["ownership"]


def test_changed_revision_refetches_source_and_never_mixes_cached_rows(collection):
    store, _, lease, clock, _ = collection
    partial_run(store, lease)
    scripted = pages()
    for (member, source, cursor), value in scripted.items():
        if source == "ownership":
            value["source_revision"] = "new-revision"
            value["rows"][0]["grade"] = 2
    port = MockCollection(scripted, True)
    result = run_collection_job(store, retry(store, clock), port, TIME, ComputeBudget())
    assert result.state == "succeeded"
    assert len([call for call in port.calls if call[1] == "ownership"]) == 2
    training = store.get_document(store.get_result(result.result_id)["training_snapshot_id"])
    assert [dict(record.metrics)["breakthrough"] for record in read_training(training)[:2]] == [2, 2]


def test_auth_failure_never_falls_back_to_previously_complete_source(collection):
    store, job, lease, clock, _ = collection
    partial_run(store, lease)
    port = MockCollection({(MEMBER.key, "ownership", None): CollectionSourceError(True)}, True)
    result = run_collection_job(store, retry(store, clock), port, TIME, ComputeBudget())
    assert result.state == "auth_required" and len(port.calls) == 1
    checkpoint = store.load_checkpoint(job.job_id)
    raw = store.get_document(checkpoint["last_observation"]["raw_snapshot_id"])
    assert raw.payload["members"][0]["ownership"]["status"] == "auth_required"
    assert raw.payload["members"][0]["ownership"]["characters"] == []


@pytest.mark.parametrize("field", ["code_sha", "collection_inputs_id", "schema_version"])
def test_checkpoint_dependency_mismatch_blocks_before_source_requests(collection, field):
    store, job, lease, clock, _ = collection
    partial_run(store, lease)
    replacement = retry(store, clock)
    checkpoint = store.load_checkpoint(job.job_id)
    checkpoint[field] = "wrong"
    store.save_checkpoint(replacement, checkpoint)
    port = MockCollection(pages(), True)
    with pytest.raises(StateConflict):
        run_collection_job(store, replacement, port, TIME, ComputeBudget())
    assert port.calls == []


def test_corrupt_cached_fragment_cannot_be_promoted_after_head_verification(collection):
    store, job, lease, clock, _ = collection
    checkpoint = partial_run(store, lease)
    source = checkpoint["sources"][canonical_json(["area", "member", "ownership"])]
    store._connection.execute("UPDATE objects SET serialized=serialized||' ' WHERE object_id=?", (source["page_ids"][1],))
    port = MockCollection(pages(), True)
    with pytest.raises(StateConflict):
        run_collection_job(store, retry(store, clock), port, TIME, ComputeBudget())
    assert len(port.calls) == 1 and store.get_job(job.job_id).result_id is None


def test_partial_observation_and_page_fragments_are_retention_roots(collection):
    store, _, lease, clock, path = collection
    checkpoint = partial_run(store, lease)
    roots = {value for source in checkpoint["sources"].values() for value in source["page_ids"]}
    roots.update((checkpoint["last_observation"]["raw_snapshot_id"], checkpoint["last_observation"]["training_snapshot_id"]))
    with closing(LocalObjectRetention(path, clock)) as retention:
        plan = retention.plan_retention(clock.value+1)
        assert not roots.intersection(plan.object_ids)
        retention.apply_retention(plan, plan.plan_id)
    assert all(store.get_document(object_id) for object_id in roots)


def test_interruption_keeps_last_valid_page_and_resume_reacquires_opaque_cursor(collection):
    store, job, lease, _, _ = collection
    port = InterruptingCollection(pages())
    result = run_collection_job(store, lease, port, TIME, ComputeBudget())
    assert result.state == "capacity_blocked"
    checkpoint = store.load_checkpoint(job.job_id)
    source = checkpoint["sources"][canonical_json(["area", "member", "ownership"])]
    assert source["status"] == "partial" and len(source["page_ids"]) == 1
    assert checkpoint["last_observation"] is None
    store.reroute(job.job_id, "local", "code")
    replacement = store.claim("replacement", "code")
    resumed = MockCollection(pages(), True)
    assert run_collection_job(store, replacement, resumed, TIME, ComputeBudget()).state == "succeeded"
    assert resumed.calls[:2] == [(MEMBER.key, "ownership", None, ()), (MEMBER.key, "ownership", "owner-cursor-secret", ())]


def test_oversized_page_blocks_capacity_without_half_result(collection):
    store, job, lease, _, _ = collection
    scripted = pages()
    scripted[(MEMBER.key, "details", None)]["rows"][0]["skill1_lv"] = "x"*(1024*1024+1)
    result = run_collection_job(store, lease, MockCollection(scripted), TIME, ComputeBudget())
    assert result.state == "capacity_blocked" and result.error_code == "collection_checkpoint_page_capacity"
    assert result.result_id is None and store.load_checkpoint(job.job_id)["sources"]


def test_changed_request_scope_does_not_reuse_different_role_details(collection):
    store, _, lease, clock, _ = collection
    partial_run(store, lease, source="effects")
    scripted = pages()
    scripted[(MEMBER.key, "ownership", "owner-cursor-secret")]["rows"][0]["name_code"] = "role_c"
    for (member, source, cursor), value in scripted.items():
        if source == "ownership":
            value["source_revision"] = "changed-ownership"
    scripted[(MEMBER.key, "details", "detail-cursor-secret")]["rows"][0] = {"name_code": "role_c", "skill1_lv": 9}
    port = MockCollection(scripted, True)
    result = run_collection_job(store, retry(store, clock), port, TIME, ComputeBudget())
    assert result.state == "succeeded"
    training = store.get_document(store.get_result(result.result_id)["training_snapshot_id"])
    records = {record.name_code: record for record in read_training(training)}
    assert records["role_b"].ownership_status == "not_owned" and dict(records["role_c"].metrics)["skill_1"] == 9
    assert len([call for call in port.calls if call[1] == "details"]) == 2


def test_cancelled_collector_cannot_advance_page_checkpoint_or_finish(collection):
    store, job, lease, _, _ = collection
    port = CancellingCollection(pages(), store, job.job_id)
    with pytest.raises(LeaseLost):
        run_collection_job(store, lease, port, TIME, ComputeBudget())
    assert store.get_job(job.job_id).state == "cancelled" and store.get_job(job.job_id).result_id is None
    checkpoint = store.load_checkpoint(job.job_id)
    assert len(checkpoint["sources"][canonical_json(["area", "member", "ownership"])]["page_ids"]) == 1


def test_progress_updates_do_not_retain_a_full_manifest_for_every_page(collection):
    store, _, lease, _, _ = collection
    checkpoint = partial_run(store, lease)
    objects = [json.loads(row[0]) for row in store._connection.execute("SELECT serialized FROM objects")]
    sealed = [value for value in objects if value.get("checkpoint_kind") == "training_collection"]
    pointers = [value for value in objects if value.get("checkpoint_kind") == "training_collection_pointer"]
    fragments = [value for value in objects if value.get("payload", {}).get("fragment_kind") == "training_source_page"]
    assert len(sealed) == 1 and len(pointers) == 1 and len(fragments) == 4
    assert store.load_collection_progress(lease.job_id) == checkpoint


def test_progress_table_content_hash_prevents_silent_state_change(collection):
    store, _, lease, clock, _ = collection
    partial_run(store, lease)
    store._connection.execute("UPDATE collection_source_progress SET state_json=replace(state_json,?,?) WHERE source_key=?",
        ('"complete"', '"failed"', canonical_json(["area", "member", "ownership"])))
    port = MockCollection(pages(), True)
    with pytest.raises(StateConflict, match="integrity"):
        run_collection_job(store, retry(store, clock), port, TIME, ComputeBudget())
    assert port.calls == []


def test_retry_keeps_prior_partial_observation_as_auditable_retention_root(collection):
    store, job, lease, clock, path = collection
    prior = partial_run(store, lease)
    first = prior["last_observation"]
    old_fragments = {page_id for source in prior["sources"].values() for page_id in source["page_ids"]}
    replacement = retry(store, clock)
    assert run_collection_job(store, replacement, MockCollection(pages(), True), "2026-10-15T00:01:00Z", ComputeBudget()).state == "succeeded"
    observations = store.collection_observations(job.job_id)
    assert len(observations) == 2 and observations[0]["raw_snapshot_id"] == first["raw_snapshot_id"]
    assert observations[0]["generation"] < observations[1]["generation"]
    with closing(LocalObjectRetention(path, clock)) as retention:
        plan = retention.plan_retention(clock.value+1)
        assert first["raw_snapshot_id"] not in plan.object_ids and first["training_snapshot_id"] not in plan.object_ids
        assert observations[0]["checkpoint_id"] not in plan.object_ids and not old_fragments.intersection(plan.object_ids)


def test_opaque_cursor_rotation_does_not_require_persisting_the_new_cursor(collection):
    store, _, lease, clock, _ = collection
    partial_run(store, lease)
    scripted = pages()
    scripted[(MEMBER.key, "ownership", None)]["next_cursor"] = "rotated-cursor-secret"
    scripted.pop((MEMBER.key, "ownership", "owner-cursor-secret"))
    port = MockCollection(scripted, True)
    result = run_collection_job(store, retry(store, clock), port, "2026-10-15T00:01:00Z", ComputeBudget())
    assert result.state == "succeeded" and len([call for call in port.calls if call[1] == "ownership"]) == 1
    assert all("rotated-cursor-secret" not in row[0] for row in store._connection.execute("SELECT serialized FROM objects"))


def test_short_terminal_head_never_reuses_prior_complete_source(collection):
    store, job, lease, clock, _ = collection
    partial_run(store, lease)
    scripted = pages()
    scripted[(MEMBER.key, "ownership", None)]["next_cursor"] = None
    scripted[(MEMBER.key, "details", None)] = page("details", [{"name_code": "role_a", "skill1_lv": 5}], 1)
    result = run_collection_job(store, retry(store, clock), MockCollection(scripted, True), TIME, ComputeBudget())
    assert result.state == "retry_wait"
    raw = store.get_document(store.load_checkpoint(job.job_id)["last_observation"]["raw_snapshot_id"])
    assert raw.payload["members"][0]["ownership"]["status"] == "partial"


def test_observation_seal_and_history_rollback_together_on_database_failure(collection):
    store, job, lease, _, _ = collection
    store._connection.execute("CREATE TRIGGER mock_observation_failure BEFORE INSERT ON collection_observations BEGIN SELECT RAISE(ABORT,'mock observation failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        run_collection_job(store, lease, MockCollection(pages()), TIME, ComputeBudget())
    assert store.get_job(job.job_id).state == "running" and store.get_job(job.job_id).result_id is None
    assert store.collection_observations(job.job_id) == ()
    assert store.load_checkpoint(job.job_id)["last_observation"] is None
    store._connection.execute("DROP TRIGGER mock_observation_failure")
    assert run_collection_job(store, lease, MockCollection(pages(), True), TIME, ComputeBudget()).state == "succeeded"
    assert len(store.collection_observations(job.job_id)) == 1


class Clock:
    def __init__(self):
        self.value = 1000.0

    def __call__(self):
        return self.value


class InterruptingCollection(MockCollection):
    def fetch_page(self, member, source, cursor, requested_ids):
        if source == "ownership" and cursor is not None:
            raise ComputeLimit("mock_collection_interrupted")
        return super().fetch_page(member, source, cursor, requested_ids)


class CancellingCollection(MockCollection):
    def __init__(self, scripted, store, job_id):
        super().__init__(scripted)
        self.store, self.job_id = store, job_id

    def fetch_page(self, member, source, cursor, requested_ids):
        if source == "ownership" and cursor is not None:
            self.store.cancel(self.job_id)
        return super().fetch_page(member, source, cursor, requested_ids)
