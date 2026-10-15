from dataclasses import replace
from contextlib import closing

import pytest

from adapters.local_retention import LocalObjectRetention

from application.healthcheck import IncompleteTraining, calculate_healthcheck
from application.reference_catalog import capture_reference, read_reference
from application.reference_projection import project_reference_training, verify_reference_projection
from application.tasks import TaskSpec
from domain.healthcheck import MetricRule, RoleRule
from domain.normalization import capture_raw_training
from domain.snapshots import MemberKey, capture_config, capture_document, read_training

TIME = "2026-10-15T00:00:00Z"
MEMBER = MemberKey("area", "member")


@pytest.fixture
def source():
    reference = capture_reference("common", TIME, ("role_a", "role_b"), {"10001": "attack", "10002": "reload"},
        (("old_name", "role_a"),), (("攻", "attack"),))
    catalog, _ = read_reference(reference)
    member = {"area_id": MEMBER.area_id, "member_id": MEMBER.member_id,
        "ownership": {"status": "complete", "characters": [{"name_code": "role_a", "grade": 3, "core": 2}]},
        "details": {"status": "complete", "requested_codes": ["role_a"], "is_options_complete": True,
            "character_details": [{"name_code": "role_a", "skill1_lv": 10, "equip_option_1": "1000101"}],
            "state_effects": [{"id": "1000101", "function_details": [{"function_value": 1000}]}]}}
    raw = capture_raw_training("union", TIME, catalog.reference.version, (member,), catalog.reference.role_ids)
    return reference, raw


def test_alias_only_transition_reuses_values_without_relabeling_raw(source):
    old, raw = source
    target = capture_reference("common", TIME, ("role_a", "role_b"), {"10001": "attack", "10002": "reload"},
        (("new_name", "role_a"),), (("攻擊力", "attack"),))
    original = raw.serialized
    projected = project_reference_training(raw, old, target, "code")
    assert not projected.plan.blockers and projected.training is not None
    assert projected.training.envelope["reference_version"] == target.envelope["reference_version"]
    assert raw.serialized == original and raw.envelope["reference_version"] == old.envelope["reference_version"]
    provenance = projected.training.payload["reference_projection"]
    assert provenance["source_reference_id"] == old.snapshot_id and provenance["target_reference_id"] == target.snapshot_id
    config = capture_config("common", TIME, target.envelope["reference_version"],
        (RoleRule("role_a", ("電擊",), "output", (MetricRule("trait_1", 5, 1, "attack"),), 1),))
    result = calculate_healthcheck(config, projected.training, (MEMBER,), {}, "code")
    assert result.snapshot_id == projected.plan.training_id
    verify_reference_projection(projected, raw, old, target, "code")


def test_removed_roles_and_prefixes_produce_explicit_filtered_projection(source):
    old, raw = source
    target = capture_reference("common", TIME, ("role_a",), {"10001": "attack"})
    projected = project_reference_training(raw, old, target, "code")
    records = read_training(projected.training)
    assert {record.name_code for record in records} == {"role_a"}
    assert dict(records[0].traits) == {"attack": 10}
    assert len(read_training(projected.training)) == 1


@pytest.mark.parametrize("change,reason", [("role", "ownership_refresh_required"), ("prefix", "function_mapping_refresh_required"), ("mapping", "function_mapping_refresh_required")])
def test_added_roles_or_changed_function_semantics_cannot_reuse_absence(source, change, reason):
    old, raw = source
    roles = ("role_a", "role_b", "new") if change == "role" else ("role_a", "role_b")
    prefixes = {"10001": "attack", "10002": "reload"}
    if change == "prefix":
        prefixes["10003"] = "new_function"
    elif change == "mapping":
        prefixes["10001"] = "different_function"
    target = capture_reference("common", TIME, roles, prefixes)
    projection = project_reference_training(raw, old, target, "code")
    assert reason in projection.plan.blockers and projection.training is None


def test_missing_source_universe_is_blocked_even_for_alias_only_change(source):
    old, raw = source
    payload = raw.payload
    payload.pop("reference_role_ids")
    legacy = capture_document("raw", "union", TIME, old.envelope["reference_version"], payload)
    projection = project_reference_training(legacy, old, old, "code")
    assert projection.plan.blockers == ("role_coverage_unproven",)


def test_projection_does_not_convert_unknowns_to_complete_training(source):
    old, raw = source
    payload = raw.payload
    payload["members"][0]["details"]["character_details"] = []
    incomplete = capture_document("raw", "union", TIME, old.envelope["reference_version"], payload)
    projection = project_reference_training(incomplete, old, old, "code")
    config = capture_config("common", TIME, old.envelope["reference_version"],
        (RoleRule("role_a", ("電擊",), "output", (MetricRule("skill_1", 5, 1),), 1),))
    assert projection.training is not None
    with pytest.raises(IncompleteTraining):
        calculate_healthcheck(config, projection.training, (MEMBER,), {}, "code")


def test_projection_receipt_cannot_swap_code_training_or_source(source):
    old, raw = source
    projection = project_reference_training(raw, old, old, "code")
    with pytest.raises(ValueError, match="receipt"):
        verify_reference_projection(replace(projection, plan=replace(projection.plan, training_id="wrong")), raw, old, old, "code")
    with pytest.raises(ValueError, match="receipt"):
        verify_reference_projection(projection, raw, old, old, "other_code")
    other = capture_reference("common", TIME, ("other",), {})
    with pytest.raises(ValueError, match="dependencies"):
        project_reference_training(raw, other, old, "code")


def test_auth_required_source_cannot_be_promoted_by_reference_projection(source):
    old, raw = source
    payload = raw.payload
    payload["members"][0]["details"]["status"] = "auth_required"
    payload["members"][0]["details"]["character_details"] = []
    auth = capture_document("raw", "union", TIME, old.envelope["reference_version"], payload)
    projection = project_reference_training(auth, old, old, "code")
    assert projection.training is None and projection.plan.blockers == ("auth_required",)


def test_projection_lineage_retains_both_catalogs_and_original_raw(source, tmp_path):
    old, raw = source
    target = capture_reference("common", TIME, ("role_a",), {"10001": "attack"})
    projection = project_reference_training(raw, old, target, "code")
    with closing(LocalObjectRetention(tmp_path / "state.sqlite", clock=fixed_clock)) as store:
        for document in (old, target, raw, projection.training):
            store.put_document(document)
        store.submit_job("request", TaskSpec("prepare_layout", "union", "code", {"training": projection.training.snapshot_id}))
        plan = store.plan_retention(101)
        assert not plan.object_ids


def fixed_clock():
    return 100
