import copy

import pytest

from adapters.mock_collection import MockCollection
from adapters.local_state import LocalStateStore
from application.collection import CollectionSourceError, collect_training, run_collection_job
from application.compute_budget import ComputeBudget
from application.tasks import TaskSpec
from domain.normalization import normalize_training
from domain.snapshots import MemberKey, capture_document, read_training

MEMBER = MemberKey("area_mock", "member_mock")
ROLES = ("role_a", "role_b", "role_c")


def test_full_page_chain_proves_absence_and_scrubs_secrets():
    pages = source_pages()
    pages[(MEMBER.key, "ownership", None)]["authorization"] = "never_capture"
    pages[(MEMBER.key, "details", None)]["rows"][0]["private_key"] = "never_capture"
    port = MockCollection(pages)
    raw = collect(port)
    assert "never_capture" not in raw.serialized
    records = read_training(normalize_training(raw, ROLES, {}).training)
    assert [row.ownership_status for row in records] == ["owned", "owned", "not_owned"]
    proof = raw.payload["collection_proof"][0]["sources"]["ownership"]
    assert proof["status"] == "complete" and len(proof["pages"]) == 2
    assert port.calls[2][3] == ("role_a", "role_b")


@pytest.mark.parametrize("change", ["cycle", "revision", "total", "duplicate", "identity", "reference", "scope", "foreign", "empty_page"])
def test_invalid_evidence_never_establishes_absent_roles(change):
    pages = source_pages()
    first = pages[(MEMBER.key, "ownership", None)]
    second = pages[(MEMBER.key, "ownership", "page2")]
    if change == "cycle":
        second["next_cursor"] = "page2"
    elif change == "revision":
        second["source_revision"] = "changed"
    elif change == "total":
        second["total"] = 3
    elif change == "duplicate":
        second["rows"] = copy.deepcopy(first["rows"])
    elif change == "identity":
        second["member"]["member_id"] = "other"
    elif change == "reference":
        second["reference_version"] = "other"
    elif change == "scope":
        second["scope_id"] = "other"
    elif change == "foreign":
        second["rows"][0]["name_code"] = "unknown"
    else:
        first["rows"] = []
    raw = collect(MockCollection(pages))
    assert raw.payload["members"][0]["ownership"]["status"] == "failed"
    records = read_training(normalize_training(raw, ROLES, {}).training)
    assert all(row.ownership_status == "unknown" for row in records)


@pytest.mark.parametrize("failure", ["timeout", "page_limit", "short_terminal"])
def test_missing_pages_keep_seen_roles_but_never_infer_absence(failure):
    pages = source_pages()
    if failure == "timeout":
        pages[(MEMBER.key, "ownership", "page2")] = TimeoutError()
    elif failure == "short_terminal":
        pages[(MEMBER.key, "ownership", None)]["next_cursor"] = None
    pages[(MEMBER.key, "details", None)]["rows"] = [{"name_code": "role_a", "skill1_lv": 5}]
    pages[(MEMBER.key, "details", None)]["total"] = 1
    raw = collect(MockCollection(pages), max_pages=1 if failure == "page_limit" else 100)
    records = read_training(normalize_training(raw, ROLES, {}).training)
    assert [row.ownership_status for row in records] == ["owned", "unknown", "unknown"]


def test_auth_error_stops_member_source_requests_and_preserves_explicit_state():
    port = MockCollection({(MEMBER.key, "ownership", None): CollectionSourceError(True)})
    raw = collect(port)
    assert len(port.calls) == 1
    normalized = normalize_training(raw, ROLES, {})
    assert normalized.is_auth_required
    assert all(row.ownership_status == "unknown" for row in read_training(normalized.training))


def test_missing_effects_cannot_claim_zero_traits():
    pages = source_pages()
    pages[(MEMBER.key, "details", None)]["rows"][0]["equip_option_1"] = "1000101"
    raw = collect(MockCollection(pages))
    normalized = normalize_training(raw, ROLES, {"10001": "function_mock"})
    assert dict(read_training(normalized.training)[0].traits)["function_mock"] is None
    assert raw.payload["members"][0]["details"]["is_options_complete"] is False


def test_row_limit_rejects_page_before_accepting_records():
    raw = collect(MockCollection(source_pages()), max_rows=1)
    assert raw.payload["members"][0]["ownership"]["status"] == "failed"


@pytest.mark.parametrize("outcome", ["complete", "partial", "auth"])
def test_collection_task_seals_only_complete_observation(tmp_path, outcome):
    store = LocalStateStore(tmp_path / "state.sqlite")
    try:
        inputs = capture_document("union_inputs", "union_mock", "2026-10-15T00:00:00Z", "reference_mock",
            {"collection_kind": "training_inputs", "members": [{"area_id": MEMBER.area_id, "member_id": MEMBER.member_id}],
             "role_ids": list(ROLES), "function_prefixes": {}})
        store.put_document(inputs)
        job = store.submit_job("request", TaskSpec("refresh_roster_and_training", "union_mock", "code",
            {"collection_inputs": inputs.snapshot_id}, family="collection", intent_id="intent_mock"))
        pages = source_pages()
        if outcome == "partial":
            pages[(MEMBER.key, "ownership", "page2")] = TimeoutError()
        if outcome == "auth":
            pages[(MEMBER.key, "ownership", None)] = CollectionSourceError(True)
        lease = store.claim("worker", "code")
        result = run_collection_job(store, lease, MockCollection(pages), "2026-10-15T00:01:00Z", ComputeBudget())
        assert result.job_id == job.job_id
        assert result.state == {"complete": "succeeded", "partial": "retry_wait", "auth": "auth_required"}[outcome]
        if outcome == "complete":
            sealed = store.get_result(result.result_id)
            training = store.get_document(sealed["training_snapshot_id"])
            assert training.payload["raw_snapshot_id"] == sealed["raw_snapshot_id"]
            assert sealed["collection_inputs_id"] == inputs.snapshot_id
        else:
            assert result.result_id is None
    finally:
        store.close()


def collect(port, **limits):
    return collect_training(port, "union_mock", "2026-10-15T00:00:00Z", "reference_mock", (MEMBER,), ROLES, **limits)


def source_pages():
    return {(MEMBER.key, "ownership", None): page("ownership", [{"name_code": "role_a", "grade": 0, "core": 0}], 2, "page2"),
        (MEMBER.key, "ownership", "page2"): page("ownership", [{"name_code": "role_b", "grade": 0, "core": 0}], 2),
        (MEMBER.key, "details", None): page("details", [{"name_code": "role_a", "skill1_lv": 5}, {"name_code": "role_b", "skill1_lv": 3}], 2),
        (MEMBER.key, "effects", None): page("effects", [], 0)}


def page(source, rows, total, next_cursor=None):
    return {"member": {"area_id": MEMBER.area_id, "member_id": MEMBER.member_id}, "scope_id": "union_mock",
        "source": source, "reference_version": "reference_mock", "source_revision": "revision_mock",
        "total": total, "next_cursor": next_cursor, "rows": rows, "is_options_complete": True}
