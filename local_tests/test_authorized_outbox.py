from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import json
from threading import Barrier

import pytest

from adapters.local_authorized_outbox import LocalAuthorizedOutbox
from adapters.local_outbox import LocalOutbox
from adapters.local_routing_registry import LocalRoutingRegistry
from adapters.local_system_status import read_system_status
from adapters.mock_discord import MockDiscord
from adapters.mock_sheets import MockSheets
from application.delivery import DeliveryClaim, send_next
from application.publication import publish_workbooks
from application.report_delivery import ReportCandidateValidator, compile_report_candidate
from application.routing_registry import RoutingAdministrator, SenderGrant
from application.sheet_healthcheck import run_healthcheck_job
from application.tasks import ResourceBusy, StateConflict
from domain.snapshots import RoleLabel, capture_config, capture_document, read_config
from executors.local.demo import run_demo

ADMIN = RoutingAdministrator("admin", ("union_demo",))
SENDER = "sender"


@pytest.fixture
def authorized(tmp_path):
    directory = tmp_path / "mock"
    summary = run_demo(directory)
    store = LocalRoutingRegistry(directory / "state.sqlite")
    outbox = LocalAuthorizedOutbox(directory / "state.sqlite", SENDER)
    provider = MockDiscord(directory / "mock-messages.sqlite")
    try:
        old = store.get_job(summary["job_id"])
        rules, _ = read_config(store.get_document(old.spec.version_map["healthcheck_config"]))
        rules = tuple(replace(rule, metrics=tuple(replace(metric, threshold=9) for metric in rule.metrics)) for rule in rules)
        config = capture_config("common", "2026-10-15T00:05:00Z", "reference_demo", rules,
            tuple(RoleLabel(rule.name_code, "模擬角色"*600) for rule in rules))
        candidate_id = store.create_candidate(config, old.spec.version_map["healthcheck_config"])
        store.publish_candidate(candidate_id, (replace(old.spec, versions={**old.spec.version_map, "healthcheck_config": config.snapshot_id}),))
        lease = store.claim("worker", "mock-release-v1")
        computed = run_healthcheck_job(store, lease)
        result = store.get_result(computed.result_id)
        claim = store.begin_publication(computed.job_id, "publisher", "mock-release-v1", tuple(result["workbooks"]))
        publish_workbooks(store, MockSheets(directory / "workbooks"), claim, result["workbooks"], result["layout_revisions"])
        routes = capture_document("union_inputs", "union_demo", "2026-10-15T00:06:00Z", "reference_demo",
            {"routing_kind": "report_routes", "routes": [{"member": {"area_id": "area_demo", "member_id": member},
                "target_thread": "thread_mock", "mention_user_id": "123"} for member in ("member_a", "member_b")]})
        store.publish_route("route", routes, routes.snapshot_id, None, ADMIN, "code")
        grant = SenderGrant("union_demo", SENDER, ("thread_mock",), ("123",))
        store.publish_sender_grant("grant", grant, grant.grant_id, 0, ADMIN, "code")
        candidate = compile_report_candidate(store, computed.job_id, routes.snapshot_id, "send_intent")
        assert len(candidate.deliveries) == 2 and all(len(spec.parts) >= 2 for spec in candidate.deliveries)
        outbox.prepare_batch(candidate)
        yield store, outbox, provider, candidate, routes, grant, directory
    finally:
        provider.close()
        outbox.close()
        store.close()


def accept(outbox, candidate):
    return outbox.confirm_batch("confirm", candidate.candidate_id, candidate.candidate_id)


def next_route(routes):
    payload = routes.payload
    for row in payload["routes"]:
        row["target_thread"] = "new_thread"
    return capture_document("union_inputs", "union_demo", "2026-10-15T00:10:00Z", "reference_demo", payload)


def test_scoped_confirm_send_and_replay_preserve_parts_and_release_route_holds(authorized):
    store, outbox, provider, candidate, routes, _, _ = authorized
    ids = accept(outbox, candidate)
    for outbox_id in ids:
        while send_next(outbox, provider, outbox_id, "sender_worker"):
            pass
    assert len(provider.messages()) == sum(len(spec.parts) for spec in candidate.deliveries)
    assert store._connection.execute("SELECT COUNT(*) FROM authorized_route_leases").fetchone()[0] == 0
    assert accept(outbox, candidate) == ids
    changed = next_route(routes)
    store.publish_route("new_route", changed, changed.snapshot_id, routes.snapshot_id, ADMIN, "code")
    assert accept(outbox, candidate) == ids
    assert all(part["state"] == "sent" for outbox_id in ids for part in outbox.parts(outbox_id))


@pytest.mark.parametrize("change", ["disabled", "thread", "mention"])
def test_revocation_is_checked_between_parts_without_resending_first_part(authorized, change):
    store, outbox, provider, candidate, _, grant, _ = authorized
    ids = accept(outbox, candidate)
    assert send_next(outbox, provider, ids[0], "worker")
    disabled = replace(grant, is_enabled=change != "disabled", allowed_threads=("other",) if change == "thread" else grant.allowed_threads,
        allowed_mentions=() if change == "mention" else grant.allowed_mentions)
    store.publish_sender_grant("disable", disabled, disabled.grant_id, 1, ADMIN, "code")
    with pytest.raises(StateConflict):
        send_next(outbox, provider, ids[0], "worker")
    assert len(provider.messages()) == 1
    assert outbox.parts(ids[0])[0]["state"] == "sent" and outbox.parts(ids[0])[1]["state"] == "queued"


def test_changed_route_blocks_previously_accepted_unsent_delivery(authorized):
    store, outbox, provider, candidate, routes, _, _ = authorized
    ids = accept(outbox, candidate)
    changed = next_route(routes)
    store.publish_route("new_route", changed, changed.snapshot_id, routes.snapshot_id, ADMIN, "code")
    assert accept(outbox, candidate) == ids
    with pytest.raises(StateConflict):
        send_next(outbox, provider, ids[0], "worker")
    assert provider.messages() == () and outbox.parts(ids[0])[0]["attempt"] == 0


def test_unknown_send_keeps_route_locked_across_restart_and_time_until_verified(authorized):
    store, outbox, provider, candidate, routes, grant, directory = authorized
    ids = accept(outbox, candidate)
    provider.fault = "after"
    assert not send_next(outbox, provider, ids[0], "worker")
    part = outbox.parts(ids[0])[0]
    claim = DeliveryClaim(ids[0], 0, "worker", part["generation"])
    disabled = replace(grant, is_enabled=False)
    store.publish_sender_grant("disable", disabled, disabled.grant_id, 1, ADMIN, "code")
    changed = next_route(routes)
    with closing(LocalAuthorizedOutbox(directory / "state.sqlite", SENDER, clock=far_future)) as restarted:
        assert restarted.claim(ids[0], "new_worker") is None
        with pytest.raises(ResourceBusy):
            store.publish_route("new_route", changed, changed.snapshot_id, routes.snapshot_id, ADMIN, "code")
        with pytest.raises(StateConflict):
            restarted.reconcile(claim, False, None, is_absence_verified=True)
        message = provider.messages()[0]
        restarted.reconcile(claim, True, message["message_id"], message["payload"], observed_target=message["target"])
    store.publish_route("new_route", changed, changed.snapshot_id, routes.snapshot_id, ADMIN, "code")
    assert len(provider.messages()) == 1 and outbox.parts(ids[0])[0]["state"] == "sent"


def test_positive_not_sent_releases_hold_but_new_route_never_retargets_old_payload(authorized):
    store, outbox, provider, candidate, routes, _, _ = authorized
    ids = accept(outbox, candidate)
    provider.fault = "before"
    assert not send_next(outbox, provider, ids[0], "worker")
    assert store._connection.execute("SELECT COUNT(*) FROM authorized_route_leases").fetchone()[0] == 0
    changed = next_route(routes)
    store.publish_route("new_route", changed, changed.snapshot_id, routes.snapshot_id, ADMIN, "code")
    outbox.clock = far_future
    with pytest.raises(StateConflict):
        send_next(outbox, provider, ids[0], "worker")
    assert provider.messages() == ()


def test_interrupted_sender_absence_reconciliation_preserves_generation_fence(authorized):
    store, outbox, _, candidate, routes, _, _ = authorized
    ids = accept(outbox, candidate)
    old = outbox.claim(ids[0], "old_worker")
    outbox.recover_interrupted(ids[0])
    changed = next_route(routes)
    with pytest.raises(ResourceBusy):
        store.publish_route("new_route", changed, changed.snapshot_id, routes.snapshot_id, ADMIN, "code")
    outbox.reconcile(old, True, None, is_absence_verified=True)
    new = outbox.claim(ids[0], "new_worker")
    assert new.generation > old.generation
    with pytest.raises(StateConflict):
        outbox.sent(old, "forged_old_receipt")
    assert store._connection.execute("SELECT generation FROM authorized_route_leases").fetchone()[0] == new.generation


def test_different_sender_cannot_adopt_batch_or_settle_claim(authorized):
    _, outbox, _, candidate, _, _, directory = authorized
    ids = accept(outbox, candidate)
    claim = outbox.claim(ids[0], "worker")
    with closing(LocalAuthorizedOutbox(directory / "state.sqlite", "other_sender")) as other:
        with pytest.raises(StateConflict):
            other.confirm_batch("alias", candidate.candidate_id, candidate.candidate_id)
        with pytest.raises(StateConflict):
            other.claim(ids[0], "worker")
        with pytest.raises(StateConflict):
            other.sent(claim, "message")
    assert outbox.parts(ids[0])[0]["state"] == "sending"


def test_authorized_batch_rolls_back_all_insertions_when_later_delivery_is_legacy(authorized):
    store, outbox, _, candidate, _, _, directory = authorized
    legacy_spec = candidate.deliveries[1]
    with closing(LocalOutbox(directory / "state.sqlite")) as legacy:
        legacy.enqueue("legacy", legacy_spec, legacy_spec.outbox_id)
    with pytest.raises(StateConflict, match="retroactively"):
        accept(outbox, candidate)
    assert store._connection.execute("SELECT COUNT(*) FROM authorized_report_batches").fetchone()[0] == 0
    assert store._connection.execute("SELECT COUNT(*) FROM authorized_report_deliveries").fetchone()[0] == 0
    assert store._connection.execute("SELECT is_accepted FROM report_send_candidates WHERE candidate_id=?", (candidate.candidate_id,)).fetchone()[0] == 0
    assert outbox.parts(candidate.deliveries[0].outbox_id) == ()


def test_direct_enqueue_and_caller_validator_cannot_bypass_scoped_confirmation(authorized):
    _, outbox, _, candidate, _, _, _ = authorized
    spec = candidate.deliveries[0]
    with pytest.raises(StateConflict):
        outbox.enqueue("direct", spec, spec.outbox_id)
    with pytest.raises(StateConflict):
        outbox.confirm_batch("confirm", candidate.candidate_id, candidate.candidate_id, lambda candidate: None)
    assert outbox.parts(spec.outbox_id) == ()


def test_route_change_and_claim_cannot_both_win_the_same_database_race(authorized):
    _, outbox, _, candidate, routes, _, directory = authorized
    ids = accept(outbox, candidate)
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        sender = pool.submit(race_claim, directory / "state.sqlite", ids[0], barrier)
        editor = pool.submit(race_change, directory / "state.sqlite", routes, barrier)
        outcomes = {sender.result(), editor.result()}
    assert outcomes in ({"claimed", "busy"}, {"changed", "denied"})


def test_receipt_saved_before_cleanup_crash_can_release_only_positive_hold(authorized, monkeypatch):
    store, outbox, provider, candidate, routes, _, _ = authorized
    ids = accept(outbox, candidate)
    claim = outbox.claim(ids[0], "worker")
    spec = outbox.spec(ids[0])
    assert outbox.before_send(claim)
    message_id = provider.send(spec.target_thread, spec.payload(claim.ordinal))
    monkeypatch.setattr(outbox, "_release_closed", fail_after_receipt)
    with pytest.raises(OSError):
        outbox.sent(claim, message_id)
    assert outbox.parts(ids[0])[0]["state"] == "sent"
    assert store._connection.execute("SELECT COUNT(*) FROM authorized_route_leases").fetchone()[0] == 1
    changed = next_route(routes)
    store.publish_route("new_route", changed, changed.snapshot_id, routes.snapshot_id, ADMIN, "code")
    assert store._connection.execute("SELECT COUNT(*) FROM authorized_route_leases").fetchone()[0] == 0
    assert len(provider.messages()) == 1


def test_new_compute_target_does_not_mutate_already_confirmed_send_intent(authorized):
    store, outbox, provider, candidate, _, _, _ = authorized
    ids = accept(outbox, candidate)
    old = store.get_job(candidate.job_id)
    store.submit_job("new_compute", replace(old.spec, versions={**old.spec.version_map, "new_target": "next"}), is_desired=True)
    assert not store.is_job_current(old.job_id)
    assert accept(outbox, candidate) == ids
    assert send_next(outbox, provider, ids[0], "worker")
    assert provider.messages()[0]["payload"] == candidate.deliveries[0].payload(0)


def test_new_intent_cannot_bypass_unknown_outcome_for_identical_report_delivery(authorized):
    store, outbox, provider, candidate, routes, _, _ = authorized
    ids = accept(outbox, candidate)
    provider.fault = "after"
    assert not send_next(outbox, provider, ids[0], "old_worker")
    fresh = compile_report_candidate(store, candidate.job_id, routes.snapshot_id, "different_intent")
    outbox.prepare_batch(fresh)
    fresh_ids = outbox.confirm_batch("new_confirm", fresh.candidate_id, fresh.candidate_id)
    with pytest.raises(StateConflict, match="prior intent"):
        send_next(outbox, provider, fresh_ids[0], "new_worker")
    assert len(provider.messages()) == 1
    assert outbox.parts(fresh_ids[0])[0]["attempt"] == 0


@pytest.mark.parametrize("change", ["revoke", "deadline"])
def test_unused_start_permit_is_rechecked_and_cancelled_without_provider_call(authorized, change):
    store, outbox, provider, candidate, _, grant, _ = authorized
    ids = accept(outbox, candidate)
    claim = outbox.claim(ids[0], "worker")
    if change == "revoke":
        disabled = replace(grant, is_enabled=False)
        store.publish_sender_grant("disable", disabled, disabled.grant_id, 1, ADMIN, "code")
    else:
        outbox.clock = far_future
    assert outbox.before_send(claim) is False
    assert outbox.parts(ids[0])[0]["error_code"] == "sender_not_started"
    assert store._connection.execute("SELECT COUNT(*) FROM authorized_route_leases").fetchone()[0] == 0
    assert provider.messages() == ()
    with pytest.raises(StateConflict):
        outbox.before_send(claim)


def test_start_permit_is_single_use_and_revocation_cannot_recall_issued_attempt(authorized):
    store, outbox, provider, candidate, _, grant, _ = authorized
    ids = accept(outbox, candidate)
    claim = outbox.claim(ids[0], "worker")
    assert outbox.before_send(claim)
    disabled = replace(grant, is_enabled=False)
    store.publish_sender_grant("disable", disabled, disabled.grant_id, 1, ADMIN, "code")
    with pytest.raises(StateConflict, match="consumed"):
        outbox.before_send(claim)
    spec = outbox.spec(ids[0])
    message = provider.send(spec.target_thread, spec.payload(0))
    outbox.sent(claim, message)
    assert len(provider.messages()) == 1
    with pytest.raises(StateConflict):
        outbox.claim(ids[0], "next_worker")


def test_legacy_accepted_batch_cannot_be_adopted_as_authorized_sender_work(authorized):
    store, outbox, provider, candidate, _, _, directory = authorized
    with closing(LocalOutbox(directory / "state.sqlite")) as legacy:
        ids = legacy.confirm_batch("legacy_confirm", candidate.candidate_id, candidate.candidate_id, ReportCandidateValidator(store))
    with pytest.raises(StateConflict):
        accept(outbox, candidate)
    with pytest.raises(StateConflict):
        outbox.claim(ids[0], "worker")
    assert provider.messages() == ()


def test_status_exposes_route_hold_phase_without_report_body(authorized):
    _, outbox, provider, candidate, routes, _, directory = authorized
    ids = accept(outbox, candidate)
    provider.fault = "after"
    assert not send_next(outbox, provider, ids[0], "worker")
    status = read_system_status(directory, ("union_demo",))
    assert status["global"]["route_heads"][0]["route_snapshot_id"] == routes.snapshot_id
    hold = status["global"]["route_holds"][0]
    assert hold["is_started"] == 1 and hold["outbox_id"] == ids[0]
    assert "模擬角色" not in json.dumps(status, ensure_ascii=False)


def fail_after_receipt(*args):
    raise OSError("mock stopped after durable receipt")


def race_claim(path, outbox_id, barrier):
    with closing(LocalAuthorizedOutbox(path, SENDER)) as outbox:
        barrier.wait()
        try:
            outbox.claim(outbox_id, "race_worker")
            return "claimed"
        except StateConflict:
            return "denied"


def race_change(path, routes, barrier):
    with closing(LocalRoutingRegistry(path)) as store:
        barrier.wait()
        changed = next_route(routes)
        try:
            store.publish_route("race_route", changed, changed.snapshot_id, routes.snapshot_id, ADMIN, "code")
            return "changed"
        except ResourceBusy:
            return "busy"


def far_future():
    return 1000000000000.0
