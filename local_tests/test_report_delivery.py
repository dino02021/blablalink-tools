from dataclasses import replace

import pytest

from adapters.local_state import LocalStateStore
from adapters.local_outbox import LocalOutbox
from adapters.mock_discord import MockDiscord
from application.delivery import DeliverySpec, send_next
from application.report_delivery import (
    FENCE, ReportCandidateValidator, ReportSendCandidate,
    compile_report_candidate, format_report_parts,
)
from application.tasks import StateConflict
from domain.snapshots import capture_document
from executors.local.demo import run_demo


@pytest.fixture
def context(tmp_path):
    directory = tmp_path / "demo"
    summary = run_demo(directory)
    store = LocalStateStore(directory / "state.sqlite")
    outbox = LocalOutbox(directory / "state.sqlite")
    routes = capture_document("union_inputs", "union_demo", "2026-10-15T00:04:00Z", "reference_demo",
        {"routing_kind": "report_routes", "routes": [
            {"member": {"area_id": "area_demo", "member_id": "member_b"},
             "target_thread": "thread_mock", "mention_user_id": "123"}]})
    store.put_document(routes)
    yield store, outbox, summary, routes, directory
    outbox.close()
    store.close()


@pytest.mark.parametrize("body", [("長句😀" * 1600), ("測試😀\n" * 1500)], ids=["long_line", "many_lines"])
def test_parts_preserve_body_and_complete_fences(body):
    parts = format_report_parts("@mock\n" + FENCE + "cs\n" + body + "\n" + FENCE, "123")
    assert len(parts) > 1
    assert all(len(p.encode("utf-16-le")) // 2 <= 2000 and p.count(FENCE) == 2 for p in parts)
    assert parts[0].startswith("<@123> mock\n")
    assert all(p.startswith(FENCE + "cs\n") for p in parts[1:])
    assert "".join(p.split(FENCE + "cs\n", 1)[1][:-4] for p in parts) == body


def test_prepare_confirm_send_and_restart_are_idempotent(context):
    store, outbox, summary, routes, directory = context
    candidate = compile_report_candidate(store, summary["job_id"], routes.snapshot_id, "intent_mock")
    assert len(candidate.deliveries) == 1
    outbox.prepare_batch(candidate)
    assert outbox.parts(candidate.deliveries[0].outbox_id) == ()
    validator = ReportCandidateValidator(store)
    ids = outbox.confirm_batch("confirm_mock", candidate.candidate_id, candidate.candidate_id, validator)
    provider = MockDiscord(directory / "messages.sqlite")
    try:
        assert send_next(outbox, provider, ids[0], "worker_mock")
        assert not send_next(outbox, provider, ids[0], "worker_mock")
        assert outbox.confirm_batch("confirm_alias", candidate.candidate_id, candidate.candidate_id, validator) == ids
        message, = provider.messages()
        assert message["target"] == "thread_mock"
        assert message["payload"]["allowed_mentions"]["users"] == ["123"]
        restarted = LocalOutbox(directory / "state.sqlite")
        try:
            assert restarted.batch(candidate.candidate_id) == candidate
            assert restarted.parts(ids[0])[0]["state"] == "sent"
        finally:
            restarted.close()
    finally:
        provider.close()


def test_confirmation_revalidates_content_against_sealed_artifact(context):
    store, outbox, summary, routes, _ = context
    candidate = compile_report_candidate(store, summary["job_id"], routes.snapshot_id, "intent_mock")
    forged = replace(candidate, deliveries=(replace(candidate.deliveries[0], target_thread="other_thread"),))
    outbox.prepare_batch(forged)
    with pytest.raises(StateConflict):
        outbox.confirm_batch("confirm_mock", forged.candidate_id, forged.candidate_id, ReportCandidateValidator(store))
    assert outbox.parts(forged.deliveries[0].outbox_id) == ()


@pytest.mark.parametrize("is_confirmed", [False, True])
def test_new_head_blocks_unconfirmed_but_preserves_accepted_replay(context, is_confirmed):
    store, outbox, summary, routes, _ = context
    candidate = compile_report_candidate(store, summary["job_id"], routes.snapshot_id, "intent_mock")
    outbox.prepare_batch(candidate)
    validator = ReportCandidateValidator(store)
    if is_confirmed:
        outbox.confirm_batch("confirm", candidate.candidate_id, candidate.candidate_id, validator)
    pending = summary["pending_candidate_id"]
    config = store.candidate_document(pending)
    old = store.get_job(summary["job_id"])
    target = replace(old.spec, versions={**old.spec.version_map, "healthcheck_config": config.snapshot_id})
    store.publish_candidate(pending, (target,))
    if is_confirmed:
        assert outbox.confirm_batch("alias", candidate.candidate_id, candidate.candidate_id, validator)
    else:
        with pytest.raises(StateConflict):
            outbox.confirm_batch("confirm", candidate.candidate_id, candidate.candidate_id, validator)
        assert outbox.parts(candidate.deliveries[0].outbox_id) == ()


@pytest.mark.parametrize("change", ["missing", "duplicate", "scope", "reference"])
def test_routes_must_match_complete_fixed_source(context, change):
    store, _, summary, routes, _ = context
    payload = routes.payload
    if change == "missing":
        payload["routes"] = []
    if change == "duplicate":
        payload["routes"] *= 2
    modified = capture_document("union_inputs", "other" if change == "scope" else "union_demo",
        "2026-10-15T00:05:00Z", "other" if change == "reference" else "reference_demo", payload)
    store.put_document(modified)
    with pytest.raises((ValueError, StateConflict)):
        compile_report_candidate(store, summary["job_id"], modified.snapshot_id, "intent_mock")


def test_batch_rolls_back_all_members_on_later_request_conflict(context):
    _, outbox, _, _, _ = context
    first = DeliverySpec("intent", "report", "member_a", "routes", "thread", ("first",))
    second = replace(first, member_id="member_b")
    candidate = ReportSendCandidate("scope", "intent", "job", "result", "routes", (first, second))
    outbox.prepare_batch(candidate)
    conflicting = replace(second, parts=("other",))
    outbox.enqueue(f"batch:{candidate.candidate_id}:1", conflicting, conflicting.outbox_id)
    with pytest.raises(StateConflict):
        outbox.confirm_batch("confirm", candidate.candidate_id, candidate.candidate_id, accept_test_candidate)
    assert outbox.parts(first.outbox_id) == ()
    assert outbox.connection.execute("SELECT is_accepted FROM report_send_candidates").fetchone()[0] == 0


def accept_test_candidate(candidate):
    """Transaction-only fixture; production validation is tested separately."""
    assert candidate.deliveries
