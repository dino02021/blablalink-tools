from dataclasses import replace

import pytest

from adapters.sheet_standard import StandardReference
from application.training_reuse import plan_training_reuse
from domain.healthcheck import MetricRule, RoleRule
from domain.normalization import capture_raw_training, normalize_training
from domain.snapshots import MemberKey, Override, capture_config

ROLES = ("role_a", "role_b")
PREFIXES = {"10001": "attack", "10002": "reload"}
REFERENCE = StandardReference("reference", ROLES, tuple(PREFIXES.values()))
MEMBER = MemberKey("area", "member")
TIME = "2026-10-15T00:00:00Z"


@pytest.fixture
def source():
    return {"area_id": MEMBER.area_id, "member_id": MEMBER.member_id,
        "ownership": {"status": "complete", "characters": [{"name_code": "role_a", "grade": 3, "core": 2}]},
        "details": {"status": "complete", "requested_codes": ["role_a"], "is_options_complete": True,
            "character_details": [{"name_code": "role_a", "skill1_lv": 10, "equip_option_1": "1000101", "equip_option_2": "1000201"}],
            "state_effects": [{"id": "1000101", "function_details": [{"function_value": -1234}]},
                {"id": "1000201", "function_details": [{"function_value": 500}]}]}}


def capture(source, roles=ROLES):
    return capture_raw_training("union", TIME, "reference", (source,), roles)


def config(metric=MetricRule("skill_1", 5, 1), role="role_a", reference="reference"):
    return capture_config("common", TIME, reference, (RoleRule(role, ("電擊",), "output", (metric,), 1),))


def plan(config, raw, **kwargs):
    return plan_training_reuse(config, raw, kwargs.pop("reference", REFERENCE), kwargs.pop("prefixes", PREFIXES),
        kwargs.pop("members", (MEMBER,)), kwargs.pop("names", {MEMBER.key: "same"}), "code", **kwargs)


def test_threshold_and_selected_trait_changes_reuse_same_raw_and_training(source):
    raw = capture(source)
    results = [plan(config(metric), raw) for metric in (
        MetricRule("skill_1", 5, 1), MetricRule("skill_1", 11, 1),
        MetricRule("trait_1", 1, 1, "attack"), MetricRule("trait_1", 10, 1, "reload"))]
    assert all(result.is_ready and not result.requests for result in results)
    assert len({result.raw_snapshot_id for result in results}) == 1
    assert len({result.training_id for result in results}) == 1
    assert len({result.calculation_id for result in results}) == 4


def test_newly_selected_known_not_owned_role_needs_no_fetch(source):
    result = plan(config(role="role_b"), capture(source))
    assert result.is_ready and not result.requests


def test_owned_missing_details_plans_only_that_role(source):
    source["details"]["character_details"] = []
    result = plan(config(), capture(source))
    assert not result.is_ready and result.requests[0].source == "details"
    assert result.requests[0].role_ids == ("role_a",) and not result.requests[0].option_ids


def test_missing_breakthrough_requests_ownership_not_details(source):
    source["ownership"]["characters"][0].pop("grade")
    result = plan(config(MetricRule("breakthrough", 3, 1)), capture(source))
    assert [request.source for request in result.requests] == ["ownership"]


def test_missing_effect_targets_matching_option_identity(source):
    source["details"]["state_effects"] = []
    result = plan(config(MetricRule("trait_1", 1, 1, "reload")), capture(source))
    assert [request.source for request in result.requests] == ["effects"]
    assert result.requests[0].option_ids == ("1000201",)


def test_partial_option_coverage_requires_details_and_effects(source):
    source["details"]["is_options_complete"] = False
    result = plan(config(MetricRule("trait_1", 1, 1, "attack")), capture(source))
    assert [request.source for request in result.requests] == ["details", "effects"]
    assert all(not request.option_ids for request in result.requests)


def test_manual_override_reuses_raw_without_changing_it(source):
    source["details"]["character_details"][0]["skill1_lv"] = None
    raw = capture(source)
    original = raw.serialized
    result = plan(config(), raw, overrides=(Override(MEMBER, "role_a", "metric", "skill_1", value=10),))
    assert result.is_ready and raw.serialized == original


def test_auth_required_never_creates_supplementary_requests(source):
    source["details"]["status"] = "auth_required"
    source["details"]["character_details"] = []
    result = plan(config(), capture(source))
    assert result.blockers == ("auth_required",) and not result.requests


def test_reference_change_or_unknown_mapping_is_blocked_not_relabelled(source):
    raw = capture(source)
    changed = plan(config(reference="new"), raw)
    assert changed.blockers == ("reference_reconciliation_required",) and changed.training_id is None
    unknown = plan(config(MetricRule("trait_1", 1, 1, "unmapped")), raw)
    assert unknown.blockers == ("reference_mapping_required",) and not unknown.requests


def test_missing_role_universe_cannot_prove_new_role_absence(source):
    legacy = capture_raw_training("union", TIME, "reference", (source,))
    result = plan(config(role="role_b"), legacy)
    assert result.blockers == ("role_coverage_unproven",) and result.training_id is None
    pinned = capture(source)
    with pytest.raises(ValueError, match="universe"):
        normalize_training(pinned, (*ROLES, "new_role"), PREFIXES)
    changed = plan(config(), pinned, reference=replace(REFERENCE, role_ids=(*ROLES, "new_role")))
    assert changed.blockers == ("role_coverage_unproven",)


def test_new_roster_member_gets_ownership_request_and_stable_plan_hash(source):
    raw = capture(source)
    new = MemberKey("other_area", "member")
    first = plan(config(), raw, members=(MEMBER, new))
    assert len(first.requests) == 1 and first.requests[0].member == new
    assert first.requests[0].source == "ownership"
    assert first.plan_id == plan(config(), raw, members=(MEMBER, new)).plan_id
    assert first.plan_id != plan(config(), raw, members=(MEMBER, new), names={MEMBER.key: "renamed"}).plan_id


def test_source_universe_rejects_foreign_role_rows(source):
    with pytest.raises(ValueError, match="universe"):
        capture(source, roles=("role_b",))
