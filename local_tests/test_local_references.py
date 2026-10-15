from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier

import pytest

from adapters.local_references import LocalReferences
from adapters.local_retention import LocalObjectRetention
from application.reference_catalog import capture_reference, read_reference
from application.tasks import StateConflict
from application.training_reuse import plan_training_reuse
from domain.healthcheck import MetricRule, RoleRule
from domain.normalization import capture_raw_training
from domain.snapshots import MemberKey, capture_config, capture_document

TIME = "2026-10-15T00:00:00Z"


def reference(roles=("role_a",), captured_at=TIME):
    return capture_reference("common", captured_at, roles, {"10001": "attack"},
        tuple((f"mock_{role}", role) for role in roles), (("攻擊力", "attack"),))


def publish(store, document, base=None, request="request"):
    candidate = store.create_reference_candidate(document, base, "code")
    return store.publish_reference(request, candidate, candidate, base, "actor", "code")


def test_content_version_is_timestamp_independent_and_dictionary_round_trips():
    before = reference()
    later = reference(captured_at="2026-10-15T00:01:00Z")
    assert before.snapshot_id != later.snapshot_id
    assert before.envelope["reference_version"] == later.envelope["reference_version"]
    catalog, prefixes = read_reference(before)
    assert catalog.reference.version == before.envelope["reference_version"]
    assert catalog.reference.role_ids == ("role_a",) and prefixes == {"10001": "attack"}
    forged = capture_document("union_inputs", "common", TIME, "friendly_version", before.payload)
    with pytest.raises(ValueError, match="version"):
        read_reference(forged)


def test_candidate_deduplicates_capture_time_and_publication_survives_restart(tmp_path):
    path = tmp_path / "state.sqlite"
    with closing(LocalReferences(path)) as store:
        first = store.create_reference_candidate(reference(), None, "code")
        second = store.create_reference_candidate(reference(captured_at="2026-10-15T00:01:00Z"), None, "code")
        assert first == second and store.reference_head("common") is None
        receipt = store.publish_reference("request", first, first, None, "actor", "code")
        assert receipt["revision"] == 1
        assert store.reference_head("common")["reference_id"] == reference().snapshot_id
        assert not store.list_jobs() and store.config_head("healthcheck", "common") is None
    with closing(LocalReferences(path)) as restarted:
        assert restarted.reference_publication(receipt["publication_id"]) == receipt
        assert restarted.publish_reference("request", first, first, None, "actor", "code") == receipt


def test_old_receipt_replay_does_not_restore_old_head(tmp_path):
    with closing(LocalReferences(tmp_path / "state.sqlite")) as store:
        first = publish(store, reference())
        newer_document = reference(("role_a", "role_b"))
        newer = publish(store, newer_document, first["reference_id"], request="new")
        replay = store.publish_reference("alias", first["candidate_id"], first["candidate_id"], None, "actor", "code")
        assert replay == first
        assert store.reference_head("common")["reference_id"] == newer["reference_id"]


@pytest.mark.parametrize("change", ["confirmed", "base", "code", "actor", "request"])
def test_publication_cannot_replace_reviewed_or_replayed_intent(tmp_path, change):
    with closing(LocalReferences(tmp_path / "state.sqlite")) as store:
        first = publish(store, reference())
        current = reference(("role_a", "role_b"))
        candidate = store.create_reference_candidate(current, first["reference_id"], "code")
        if change == "actor":
            candidate, base = first["candidate_id"], None
        else:
            base = first["reference_id"]
        with pytest.raises(StateConflict):
            store.publish_reference("request" if change in ("actor", "request") else "new", candidate,
                "wrong" if change == "confirmed" else candidate, "wrong" if change == "base" else base,
                "other" if change == "actor" else "actor", "other" if change == "code" else "code")
        assert store.reference_head("common")["reference_id"] == first["reference_id"]


def test_two_connections_cannot_publish_different_catalogs_over_same_base(tmp_path):
    path = tmp_path / "state.sqlite"
    with closing(LocalReferences(path)) as store:
        base = publish(store, reference())["reference_id"]
        candidates = tuple(store.create_reference_candidate(reference(("role_a", role)), base, "code") for role in ("role_b", "role_c"))
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(_race_publish, (path,)*2, candidates, (base,)*2, (barrier,)*2))
    assert results.count("conflict") == 1
    with closing(LocalReferences(path)) as store:
        assert store.reference_head("common")["revision"] == 2


def test_new_reference_never_relabels_old_raw_or_advances_config(tmp_path):
    member = MemberKey("area", "member")
    old = reference()
    catalog, prefixes = read_reference(old)
    source = {"area_id": "area", "member_id": "member", "ownership": {"status": "complete", "characters": []},
        "details": {"status": "complete", "requested_codes": [], "is_options_complete": True, "character_details": [], "state_effects": []}}
    raw = capture_raw_training("union", TIME, catalog.reference.version, (source,), catalog.reference.role_ids)
    original = raw.serialized
    with closing(LocalReferences(tmp_path / "state.sqlite")) as store:
        base = publish(store, old)
        new = reference(("role_a", "role_b"))
        publish(store, new, base["reference_id"], request="new")
        next_catalog, next_prefixes = read_reference(new)
        config = capture_config("common", TIME, next_catalog.reference.version,
            (RoleRule("role_b", ("電擊",), "output", (MetricRule("skill_1", 5, 1),), 1),))
        plan = plan_training_reuse(config, raw, next_catalog.reference, next_prefixes, (member,), {}, "code")
        assert plan.blockers == ("reference_reconciliation_required",)
        assert raw.serialized == original and not store.list_jobs()
        assert store.config_head("healthcheck", "common") is None


@pytest.mark.parametrize("prefixes", [{"bad": "attack"}, {"10001": "same", "10002": "same"}])
def test_invalid_prefix_catalog_cannot_be_captured(prefixes):
    with pytest.raises(ValueError):
        capture_reference("common", TIME, ("role_a",), prefixes)


def test_reference_candidates_and_old_publications_remain_retention_roots(tmp_path):
    path = tmp_path / "state.sqlite"
    old, pending = reference(), reference(("role_a", "role_b"))
    with closing(LocalReferences(path, clock=fixed_clock)) as store:
        receipt = publish(store, old)
        store.create_reference_candidate(pending, receipt["reference_id"], "code")
        orphan = store.put_document(capture_document("union_inputs", "union", TIME, "unrelated", {"kind": "orphan"}))
    with closing(LocalObjectRetention(path, clock=fixed_clock)) as store:
        plan = store.plan_retention(101)
        assert orphan in plan.object_ids
        assert old.snapshot_id not in plan.object_ids and pending.snapshot_id not in plan.object_ids
        store.apply_retention(plan, plan.plan_id)
        assert store.get_document(old.snapshot_id) == old and store.get_document(pending.snapshot_id) == pending


def fixed_clock():
    return 100


def _race_publish(path, candidate, base, barrier):
    with closing(LocalReferences(path)) as store:
        barrier.wait()
        try:
            return store.publish_reference(candidate, candidate, candidate, base, "actor", "code")["publication_id"]
        except StateConflict:
            return "conflict"
