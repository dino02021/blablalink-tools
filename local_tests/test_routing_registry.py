from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from threading import Barrier

import pytest

from adapters.local_routing_registry import LocalRoutingRegistry
from application.report_delivery import compile_report_candidate
from application.routing_registry import RoutingAdministrator, SenderGrant
from application.tasks import StateConflict
from domain.snapshots import capture_document
from executors.local.demo import run_demo

ADMIN = RoutingAdministrator("admin_mock", ("union_demo",))
SENDER = "sender_mock"


@pytest.fixture
def routing(tmp_path):
    directory = tmp_path / "mock"
    summary = run_demo(directory)
    with closing(LocalRoutingRegistry(directory / "state.sqlite")) as store:
        document = route()
        store.put_document(document)
        candidate = compile_report_candidate(store, summary["job_id"], document.snapshot_id, "send_intent")
        yield store, document, candidate, directory


def route(thread="thread_mock", captured="2026-10-15T00:04:00Z"):
    return capture_document("union_inputs", "union_demo", captured, "reference_demo",
        {"routing_kind": "report_routes", "routes": [{"member": {"area_id": "area_demo", "member_id": "member_b"},
            "target_thread": thread, "mention_user_id": "123"}]})


def enable(store, document):
    receipt = store.publish_route("route", document, document.snapshot_id, None, ADMIN, "code")
    grant = SenderGrant("union_demo", SENDER, ("thread_mock",), ("123",))
    store.publish_sender_grant("grant", grant, grant.grant_id, 0, ADMIN, "code")
    return receipt, grant


def test_published_head_and_grant_allow_only_fixed_candidate_preview(routing):
    store, document, candidate, _ = routing
    with pytest.raises(StateConflict):
        store.preview_report_authorization(candidate, SENDER)
    enable(store, document)
    preview = store.preview_report_authorization(candidate, SENDER)
    assert preview["is_preview_only"] and preview["candidate_id"] == candidate.candidate_id
    assert preview["route_snapshot_id"] == document.snapshot_id and preview["grant_revision"] == 1
    assert store._connection.execute("SELECT COUNT(*) FROM report_outbox").fetchone()[0] == 0


@pytest.mark.parametrize("change", ["thread", "mention", "disabled", "principal"])
def test_destination_mention_disabled_or_different_sender_is_rejected(routing, change):
    store, document, candidate, _ = routing
    _, grant = enable(store, document)
    if change == "principal":
        principal = "different"
    else:
        principal = SENDER
        updated = replace(grant, allowed_threads=("other_thread",) if change == "thread" else grant.allowed_threads,
            allowed_mentions=() if change == "mention" else grant.allowed_mentions, is_enabled=change != "disabled")
        store.publish_sender_grant("revoke", updated, updated.grant_id, 1, ADMIN, "code")
    with pytest.raises(StateConflict):
        store.preview_report_authorization(candidate, principal)


def test_route_head_change_invalidates_preview_but_old_request_replay_cannot_restore_head(routing):
    store, document, candidate, _ = routing
    first, _ = enable(store, document)
    next_route = route("new_thread", "2026-10-15T00:05:00Z")
    store.publish_route("new_route", next_route, next_route.snapshot_id, document.snapshot_id, ADMIN, "code")
    with pytest.raises(StateConflict):
        store.preview_report_authorization(candidate, SENDER)
    assert store.publish_route("route", document, document.snapshot_id, None, ADMIN, "code") == first
    assert store.route_head("union_demo")["route_snapshot_id"] == next_route.snapshot_id


def test_revocation_replay_cannot_reenable_or_bypass_revision(routing):
    store, document, _, _ = routing
    _, grant = enable(store, document)
    original = store.publish_sender_grant("grant", grant, grant.grant_id, 0, ADMIN, "code")
    disabled = replace(grant, is_enabled=False)
    store.publish_sender_grant("disable", disabled, disabled.grant_id, 1, ADMIN, "code")
    assert store.publish_sender_grant("grant", grant, grant.grant_id, 0, ADMIN, "code") == original
    assert store.sender_grant("union_demo", SENDER)[0].is_enabled is False
    with pytest.raises(StateConflict, match="revision"):
        store.publish_sender_grant("stale", grant, grant.grant_id, 1, ADMIN, "code")


@pytest.mark.parametrize("change", ["admin_scope", "confirmation", "request", "base"])
def test_route_confirmation_and_admin_scope_cannot_be_substituted(routing, change):
    store, document, _, _ = routing
    enable(store, document)
    other = route("other", "2026-10-15T00:05:00Z")
    admin = RoutingAdministrator("other_admin", ("other_union",)) if change == "admin_scope" else ADMIN
    with pytest.raises(StateConflict):
        store.publish_route("route" if change == "request" else "new", other,
            "wrong" if change == "confirmation" else other.snapshot_id,
            None if change == "base" else document.snapshot_id, admin, "code")
    assert store.route_head("union_demo")["route_snapshot_id"] == document.snapshot_id


def test_two_connections_cannot_update_routes_from_same_base(routing):
    store, document, _, directory = routing
    enable(store, document)
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(race_route, (directory / "state.sqlite",)*2, (document.snapshot_id,)*2,
            (route("thread_a"), route("thread_b")), (barrier,)*2))
    assert results.count("conflict") == 1
    assert store.route_head("union_demo")["revision"] == 2


def test_registry_and_grants_survive_restart_with_fixed_receipts(routing):
    store, document, candidate, directory = routing
    receipt, _ = enable(store, document)
    with closing(LocalRoutingRegistry(directory / "state.sqlite")) as restarted:
        assert restarted.routing_change(receipt["change_id"]) == receipt
        assert restarted.preview_report_authorization(candidate, SENDER)["is_preview_only"]


def test_grant_and_route_shape_do_not_allow_implicit_wildcards(routing):
    store, document, _, _ = routing
    with pytest.raises(ValueError):
        SenderGrant("union_demo", SENDER, (), ())
    with pytest.raises(ValueError):
        SenderGrant("union_demo", SENDER, ("thread",), ("@everyone",))
    payload = document.payload
    payload["routes"].append(payload["routes"][0])
    duplicate = capture_document("union_inputs", "union_demo", document.envelope["captured_at"], "reference_demo", payload)
    with pytest.raises(ValueError, match="duplicate"):
        store.publish_route("bad", duplicate, duplicate.snapshot_id, None, ADMIN, "code")


def race_route(path, base, document, barrier):
    with closing(LocalRoutingRegistry(path)) as store:
        barrier.wait()
        try:
            return store.publish_route(document.snapshot_id, document, document.snapshot_id, base, ADMIN, "code")["change_id"]
        except StateConflict:
            return "conflict"
