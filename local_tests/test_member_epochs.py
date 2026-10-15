from contextlib import closing
from dataclasses import asdict, replace

import pytest

from adapters.local_state import LocalStateStore
from adapters.mock_collection import MockCollection
from adapters.mock_sheets import MockSheets
from adapters.sheet_standard import StandardReference
from application.collection import collect_training, run_collection_job
from application.compute_budget import ComputeBudget
from application.publication import publish_workbooks
from application.reference_catalog import capture_reference, read_reference
from application.reference_projection import project_reference_training
from application.roster_lifecycle import RosterMember, capture_roster_observation, prepare_roster_change
from application.sheet_healthcheck import run_healthcheck_job
from application.tasks import TaskSpec
from application.training_reuse import plan_training_reuse
from domain.healthcheck import MetricRule, RoleRule
from domain.member_epochs import validate_member_epochs
from domain.normalization import capture_raw_training, normalize_training
from domain.snapshots import MemberKey, Override, capture_config, capture_document
from executors.local.demo import raw_member, run_demo

TIME = "2026-10-15T00:10:00Z"


def test_raw_and_training_keep_valid_epoch_and_reuse_requires_same_incarnation():
    member = MemberKey("area_demo", "member_a")
    reference = StandardReference("reference_demo", ("role_demo",), ())
    raw = capture_raw_training("union", TIME, reference.version, (raw_member("member_a", 7),), reference.role_ids, {member.key: "a"*64})
    normalized = normalize_training(raw, reference.role_ids, {})
    assert normalized.training.payload["member_epochs"] == {member.key: "a"*64}
    config = capture_config("common", TIME, reference.version, (RoleRule("role_demo", ("電擊",), "output", (MetricRule("skill_1", 5, 1),), 1),))
    same = plan_training_reuse(config, raw, reference, {}, (member,), {}, "code", member_epochs={member.key: "a"*64})
    stale = plan_training_reuse(config, raw, reference, {}, (member,), {}, "code", member_epochs={member.key: "b"*64})
    assert same.is_ready and not stale.is_ready
    assert stale.requests[0].reasons == ("membership_epoch_unproven",) and stale.requests[0].member == member
    assert same.member_epoch_hash != stale.member_epoch_hash
    override = Override(member, "role_demo", "metric", "skill_1", value=99)
    assert not plan_training_reuse(config, raw, reference, {}, (member,), {}, "code", (override,), {member.key: "b"*64}).is_ready


def test_invalid_epoch_is_rejected_before_collector_requests():
    member = MemberKey("area", "member")
    port = MockCollection({})
    with pytest.raises(ValueError, match="epoch"):
        collect_training(port, "union", TIME, "reference", (member,), ("role",), member_epochs={member.key: "wrong"})
    assert port.calls == []


@pytest.mark.parametrize("value", [{"not-an-identity": "a"*64}, {'["area","other"]': "a"*64}, {'["area","member"]': False}])
def test_epoch_maps_never_guess_identity_or_coerce_values(value):
    with pytest.raises(ValueError):
        validate_member_epochs(value, (MemberKey("area", "member"),))


def test_rejoin_blocks_old_known_record_then_fresh_collection_unblocks_calculation(tmp_path):
    directory = tmp_path / "mock"
    summary = run_demo(directory)
    sheets = MockSheets(directory / "workbooks")
    original_book = sheets.read("output_demo")
    with closing(LocalStateStore(directory / "state.sqlite")) as store:
        old = store.get_job(summary["job_id"])
        current = store.get_document(old.spec.version_map["union_inputs"])
        rows = current.payload["members"]
        a, b = (MemberKey(**row["member"]) for row in rows)
        names = {MemberKey(**row["member"]): row["display_name"] for row in rows}
        leave = capture_roster_observation("union_demo", TIME, "reference_demo", (RosterMember(b, names[b]),), "complete", "leave")
        departed = prepare_roster_change(current, leave, old.spec.code_sha).target_inputs
        join = capture_roster_observation("union_demo", TIME, "reference_demo", (RosterMember(a, names[a]), RosterMember(b, names[b])), "complete", "rejoin")
        target = prepare_roster_change(departed, join, old.spec.code_sha).target_inputs
        store.put_document(target)
        spec = replace(old.spec, versions={**old.spec.version_map, "union_inputs": target.snapshot_id})
        blocked = store.submit_job("rejoin", spec, is_desired=True)
        waiting = run_healthcheck_job(store, store.claim("worker", old.spec.code_sha))
        assert waiting.job_id == blocked.job_id and waiting.state == "waiting_for_data"
        assert waiting.error_code == "training_membership_epoch_unproven" and waiting.result_id is None
        assert sheets.read("output_demo") == original_book
        collection_inputs = capture_document("union_inputs", "union_demo", TIME, "reference_demo",
            {"collection_kind": "training_inputs", "members": [asdict(member) for member in (a, b)],
                "role_ids": ["role_demo"], "function_prefixes": {}, "member_epochs": target.payload["member_epochs"]})
        store.put_document(collection_inputs)
        store.submit_job("fresh_member", TaskSpec("refresh_roster_and_training", "union_demo", old.spec.code_sha,
            {"collection_inputs": collection_inputs.snapshot_id}, family="collection", intent_id="fresh_join"))
        collected = run_collection_job(store, store.claim("collector", old.spec.code_sha), epoch_pages((a, b)), TIME, ComputeBudget())
        assert collected.state == "succeeded"
        training_id = store.get_result(collected.result_id)["training_snapshot_id"]
        assert store.get_document(training_id).payload["member_epochs"] == target.payload["member_epochs"]
        next_spec = replace(spec, versions={**spec.version_map, "training": training_id})
        next_job = store.submit_job("with_epoch_proof", next_spec, is_desired=True)
        completed = run_healthcheck_job(store, store.claim("worker", old.spec.code_sha))
        assert completed.job_id == next_job.job_id and completed.state == "computed"
        result = store.get_result(completed.result_id)
        claim = store.begin_publication(completed.job_id, "publisher", old.spec.code_sha, tuple(result["workbooks"]))
        publish_workbooks(store, sheets, claim, result["workbooks"], result["layout_revisions"])
        assert sheets.read("output_demo")["generated"]["snapshot_id"] == training_id


def test_departure_recalculates_survivor_maximum_without_recollecting(tmp_path):
    directory = tmp_path / "mock"
    summary = run_demo(directory)
    with closing(LocalStateStore(directory / "state.sqlite")) as store:
        old = store.get_job(summary["job_id"])
        current = store.get_document(old.spec.version_map["union_inputs"])
        survivor = current.payload["members"][1]
        member = MemberKey(**survivor["member"])
        observation = capture_roster_observation("union_demo", TIME, "reference_demo", (RosterMember(member, survivor["display_name"]),), "complete", "departure")
        target = prepare_roster_change(current, observation, old.spec.code_sha).target_inputs
        assert target.payload["member_epochs"] == {}
        store.put_document(target)
        job = store.submit_job("departed", replace(old.spec, versions={**old.spec.version_map, "union_inputs": target.snapshot_id}), is_desired=True)
        completed = run_healthcheck_job(store, store.claim("worker", old.spec.code_sha))
        assert completed.job_id == job.job_id and completed.state == "computed"
        result = next(iter(store.get_result(completed.result_id)["workbooks"].values()))
        assert result["snapshot_id"] == summary["training_snapshot_id"]
        assert result["scores"]["roles"] == [{"member_id": member.key, "key": "role_demo", "value": 100, "rank": None}]


def test_compatible_reference_projection_preserves_member_epoch_proof():
    member = MemberKey("area_demo", "member_a")
    source = capture_reference("common", TIME, ("role_demo",), {})
    target = capture_reference("common", TIME, ("role_demo",), {}, (("renamed", "role_demo"),))
    catalog, _ = read_reference(source)
    raw = capture_raw_training("union", TIME, catalog.reference.version, (raw_member("member_a", 7),), catalog.reference.role_ids, {member.key: "a"*64})
    projected = project_reference_training(raw, source, target, "code")
    assert projected.training.payload["member_epochs"] == {member.key: "a"*64}


def epoch_pages(members):
    pages = {}
    for index, member in enumerate(members):
        for source, rows in (("ownership", [{"name_code": "role_demo", "grade": 0, "core": 0}]),
                ("details", [{"name_code": "role_demo", "skill1_lv": 7-index*4}]), ("effects", [])):
            pages[(member.key, source, None)] = {"member": asdict(member), "scope_id": "union_demo", "source": source,
                "reference_version": "reference_demo", "source_revision": "fresh_join_revision", "total": len(rows),
                "next_cursor": None, "rows": rows, "is_options_complete": True}
    return MockCollection(pages)
