from dataclasses import asdict, replace
import hashlib

import pytest

from application.roster_lifecycle import RosterMember, capture_roster_observation, confirm_roster_change, prepare_roster_change
from domain.snapshots import MemberKey, Override, canonical_json, capture_document

A, B, C = (MemberKey("area", name) for name in ("a", "b", "c"))
TIME = "2026-10-15T00:00:00Z"


@pytest.fixture
def inputs():
    return capture_document("union_inputs", "union", TIME, "reference", {
        "members": [{"member": asdict(A), "display_name": "same", "legacy_note": "keep"}, {"member": asdict(B), "display_name": "same"}],
        "manual_member_values": {A.key: 0, B.key: ""},
        "overrides": [asdict(Override(A, "role", "metric", "skill_1", value=0)),
            asdict(Override(A, "role", "metric", "skill_2", value="", is_enabled=False)),
            asdict(Override(B, "role", "function", "attack", operation="clear"))],
        "healthcheck_route": {"resource": "mock_book", "layout_revision": "layout"}})


def observed(members=(A, B), status="complete", names=None):
    names = names or {}
    return capture_roster_observation("union", TIME, "reference", tuple(RosterMember(member, names.get(member, "same")) for member in members), status, "opaque-revision-must-not-persist")


def test_add_departure_preserves_human_values_and_assigns_new_epoch(inputs):
    original = inputs.serialized
    observation = observed((A, C))
    plan = prepare_roster_change(inputs, observation, "code")
    assert plan.added == (C,) and plan.removed == (B,) and not plan.blockers
    target = confirm_roster_change(plan, inputs, observation, "code", plan.plan_id)
    payload = target.payload
    assert payload["manual_member_values"] == {A.key: 0}
    assert payload["members"][0]["legacy_note"] == "keep"
    assert len(payload["member_epochs"][C.key]) == 64 and A.key not in payload["member_epochs"]
    archived = payload["member_archives"][0]
    assert archived["row"]["member"] == asdict(B) and archived["manual_values"] == ""
    assert archived["overrides"][0]["operation"] == "clear" and archived["overrides"][0]["value"] is None
    assert inputs.serialized == original and "opaque-revision-must-not-persist" not in observation.serialized


def test_departure_archives_zero_blank_disabled_and_opaque_row_metadata(inputs):
    plan = prepare_roster_change(inputs, observed((B,)), "code")
    archived = plan.target_inputs.payload["member_archives"][0]
    assert archived["manual_values"] == 0 and archived["row"]["legacy_note"] == "keep"
    assert [row["value"] for row in archived["overrides"]] == [0, ""]
    assert archived["overrides"][1]["is_enabled"] is False


def test_rejoin_uses_new_epoch_and_does_not_reactivate_archived_overrides(inputs):
    original = capture_document("union_inputs", "union", TIME, "reference", {**inputs.payload, "member_epochs": {A.key: "a"*64}})
    departed = prepare_roster_change(original, observed((B,)), "code").target_inputs
    rejoin = prepare_roster_change(departed, observed((A, B)), "code")
    assert rejoin.added == rejoin.rejoined == (A,)
    target = rejoin.target_inputs.payload
    assert target["member_epochs"][A.key] != "a"*64
    assert A.key not in target["manual_member_values"]
    assert all(row["member"] != asdict(A) for row in target["overrides"])
    assert target["member_archives"][0]["overrides"][0]["value"] == 0


def test_reordering_is_noop_by_default_and_explicit_order_preserves_id_values(inputs):
    observation = observed((B, A))
    unchanged = prepare_roster_change(inputs, observation, "code")
    assert unchanged.is_noop and unchanged.target_inputs.snapshot_id == inputs.snapshot_id
    reordered = prepare_roster_change(inputs, observation, "code", "observed")
    assert not reordered.is_noop and [row["member"] for row in reordered.target_inputs.payload["members"]] == [asdict(B), asdict(A)]
    assert reordered.target_inputs.payload["manual_member_values"] == inputs.payload["manual_member_values"]


def test_formula_like_rename_and_same_name_never_change_identity(inputs):
    plan = prepare_roster_change(inputs, observed(names={A: "=same", B: "=same"}), "code")
    assert len(plan.renamed) == 2 and not plan.added and not plan.removed
    assert plan.target_inputs.payload["manual_member_values"] == inputs.payload["manual_member_values"]
    assert [row["display_name"] for row in plan.target_inputs.payload["members"]] == ["=same", "=same"]


@pytest.mark.parametrize("status", ["partial", "failed", "auth_required"])
def test_incomplete_observation_cannot_archive_or_produce_publishable_target(inputs, status):
    observation = observed((A,) if status == "partial" else (), status)
    plan = prepare_roster_change(inputs, observation, "code")
    assert plan.target_inputs is None and plan.blockers and not plan.removed
    with pytest.raises(ValueError):
        confirm_roster_change(plan, inputs, observation, "code", plan.plan_id)


def test_all_members_leaving_produces_explicit_inactive_inputs_with_full_archive(inputs):
    plan = prepare_roster_change(inputs, observed(()), "code")
    target = plan.target_inputs.payload
    assert target["members"] == [] and target["roster_state"] == "inactive"
    assert target["overrides"] == [] and target["manual_member_values"] == {}
    assert len(target["member_archives"]) == 2


def test_area_change_is_departure_and_addition_not_name_based_transfer(inputs):
    moved = MemberKey("other_area", A.member_id)
    plan = prepare_roster_change(inputs, observed((moved, B)), "code")
    assert plan.added == (moved,) and plan.removed == (A,)
    assert moved.key not in plan.target_inputs.payload["manual_member_values"]


@pytest.mark.parametrize("change", ["confirmation", "code", "base", "target", "observation"])
def test_confirmation_rebuilds_fixed_plan_and_rejects_substitution(inputs, change):
    observation = observed((A, C))
    plan = prepare_roster_change(inputs, observation, "code")
    if change == "target":
        plan = replace(plan, target_inputs=inputs)
    if change == "base":
        inputs = capture_document("union_inputs", "union", TIME, "reference", {**inputs.payload, "other_note": "changed"})
    if change == "observation":
        observation = observed((A, B, C))
    with pytest.raises(ValueError):
        confirm_roster_change(plan, inputs, observation, "other" if change == "code" else "code", "wrong" if change == "confirmation" else plan.plan_id)


def test_reference_mismatch_blocks_without_relabeling_or_dropping_inputs(inputs):
    observation = observed((A, C))
    wrong = capture_document("union_inputs", "union", TIME, "different", observation.payload)
    plan = prepare_roster_change(inputs, wrong, "code")
    assert plan.blockers == ("roster_reference_transition_required",) and plan.target_inputs is None


@pytest.mark.parametrize("change", ["orphan_notes", "duplicate_override", "bad_epoch", "duplicate_member", "wrong_state"])
def test_ambiguous_or_inconsistent_existing_human_inputs_are_not_silently_migrated(inputs, change):
    payload = inputs.payload
    if change == "orphan_notes":
        payload["manual_member_values"][C.key] = "unmapped"
    elif change == "duplicate_override":
        payload["overrides"].append(payload["overrides"][0])
    elif change == "bad_epoch":
        payload["member_epochs"] = {A.key: "wrong"}
    elif change == "duplicate_member":
        payload["members"].append(payload["members"][0])
    else:
        payload["roster_state"] = "inactive"
    malformed = capture_document("union_inputs", "union", TIME, "reference", payload)
    with pytest.raises(ValueError):
        prepare_roster_change(malformed, observed((A, C)), "code")


def test_archive_tampering_is_rejected_instead_of_erasing_history(inputs):
    departed = prepare_roster_change(inputs, observed((B,)), "code").target_inputs
    payload = departed.payload
    payload["member_archives"][0]["manual_values"] = "tampered"
    malformed = capture_document("union_inputs", "union", TIME, "reference", payload)
    with pytest.raises(ValueError, match="archive"):
        prepare_roster_change(malformed, observed((A, B)), "code")


def test_archive_capacity_blocks_transition_without_deleting_history(inputs):
    first = prepare_roster_change(inputs, observed((B,)), "code").target_inputs.payload["member_archives"][0]
    archives = []
    for index in range(1000):
        body = {key: value for key, value in first.items() if key != "archive_id"}
        body["base_inputs_id"] = hashlib.sha256(str(index).encode()).hexdigest()
        archives.append({**body, "archive_id": hashlib.sha256(canonical_json(body).encode()).hexdigest()})
    current = capture_document("union_inputs", "union", TIME, "reference", {**inputs.payload, "member_archives": archives})
    plan = prepare_roster_change(current, observed((B,)), "code")
    assert plan.blockers == ("roster_archive_capacity",) and plan.target_inputs is None

def test_large_human_value_moves_to_archive_without_duplicating_active_data(inputs):
    payload = inputs.payload
    payload['manual_member_values'][A.key] = 'x' * (5*1024*1024)
    current = capture_document('union_inputs', 'union', TIME, 'reference', payload)
    plan = prepare_roster_change(current, observed((B,)), 'code')
    # Moving a value into the archive does not duplicate it in active human data.
    assert not plan.blockers
    assert len(plan.target_inputs.serialized.encode()) < 8*1024*1024
    assert plan.target_inputs.payload['member_archives'][0]['manual_values'] == payload['manual_member_values'][A.key]

def test_target_capacity_blocks_new_archive_metadata_without_truncating(inputs):
    payload = inputs.payload
    payload['manual_member_values'][A.key] = ''
    empty = capture_document('union_inputs', 'union', TIME, 'reference', payload)
    payload['manual_member_values'][A.key] = 'x' * (8*1024*1024-len(empty.serialized.encode())-10)
    current = capture_document('union_inputs', 'union', TIME, 'reference', payload)
    plan = prepare_roster_change(current, observed((B,)), 'code')
    assert plan.blockers == ('roster_input_capacity',) and plan.target_inputs is None
    assert current.payload['manual_member_values'][A.key] == payload['manual_member_values'][A.key]
