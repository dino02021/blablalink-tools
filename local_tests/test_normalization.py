import pytest

from domain.normalization import capture_raw_training, normalize_training
from domain.snapshots import read_training, capture_config, MemberKey, metric_values
from domain.healthcheck import MetricRule, RoleRule
from application.healthcheck import IncompleteTraining, calculate_healthcheck

ROLES = ("role_a", "role_b", "role_c")
FUNCTIONS = {"10001": "function_a", "10002": "function_b"}


def test_whitelist_preserves_all_options_and_effect_values_without_credentials():
    member = _member()
    member["authorization"] = "never-store"
    member["details"]["character_details"][0]["private_key"] = "never-store"
    member["details"]["state_effects"][0]["function_details"][0]["token"] = "never-store"
    raw = _capture(member)
    assert "never-store" not in raw.serialized
    assert raw.payload["members"][0]["details"]["character_details"][0]["equip_option_2"] == "1000201"
    normalized = normalize_training(raw, ROLES, FUNCTIONS)
    records = read_training(normalized.training)
    first = records[0]
    assert first.ownership_status == "owned"
    assert dict(first.metrics)["breakthrough"] == 5
    assert dict(first.metrics)["skill_1"] == 10
    assert dict(first.metrics)["equip_head"] == "T9"
    assert dict(first.metrics)["collection"] == "SR15"
    assert dict(first.traits) == {"function_a": 12.34, "function_b": 5.0}
    assert records[1].ownership_status == "not_owned"
    assert normalized.training.payload["raw_snapshot_id"] == raw.snapshot_id
    assert normalized.is_auth_required is False


@pytest.mark.parametrize("status", ["partial", "failed", "auth_required"])
def test_source_failure_or_partial_ownership_never_becomes_not_owned(status):
    member = _member()
    member["ownership"] = {"status": status, "characters": []}
    member["details"] = {"status": status, "requested_codes": [], "character_details": [], "state_effects": []}
    normalized = normalize_training(_capture(member), ROLES, FUNCTIONS)
    assert all(record.ownership_status == "unknown" for record in read_training(normalized.training))
    assert normalized.is_auth_required == (status == "auth_required")


def test_missing_owned_detail_requests_refetch_without_erasing_ownership():
    member = _member()
    member["details"]["character_details"] = []
    normalized = normalize_training(_capture(member), ROLES, FUNCTIONS)
    record = read_training(normalized.training)[0]
    assert record.ownership_status == "owned" and dict(record.metrics) == {"breakthrough": 5}
    assert normalized.missing_details == ((record.member.key, "role_a"),)


def test_repeated_trait_slots_sum_absolute_values_and_keep_untracked_function():
    member = _member()
    member["details"]["character_details"][0]["equip_option_3"] = "1000101"
    normalized = normalize_training(_capture(member), ROLES, FUNCTIONS)
    assert dict(read_training(normalized.training)[0].traits) == {"function_a": 24.68, "function_b": 5.0}


@pytest.mark.parametrize("change", ["effect_missing", "invalid_value", "options_partial"])
def test_incomplete_trait_never_silently_scores_zero_or_partial_sum(change):
    member = _member()
    if change == "effect_missing":
        member["details"]["state_effects"] = member["details"]["state_effects"][1:]
    elif change == "invalid_value":
        member["details"]["state_effects"][0]["function_details"][0]["function_value"] = "invalid"
    else:
        member["details"]["is_options_complete"] = False
    normalized = normalize_training(_capture(member), ROLES, FUNCTIONS)
    assert dict(read_training(normalized.training)[0].traits)["function_a"] is None
    assert normalized.issues


def test_explicit_complete_empty_options_can_mean_zero():
    member = _member()
    detail = member["details"]["character_details"][0]
    detail["equip_option_1"] = detail["equip_option_2"] = 0
    normalized = normalize_training(_capture(member), ROLES, FUNCTIONS)
    assert dict(read_training(normalized.training)[0].traits) == {"function_a": 0, "function_b": 0}


@pytest.mark.parametrize("change", ["duplicate_role", "duplicate_effect", "outside_request", "unknown_role", "contradiction"])
def test_ambiguous_or_conflicting_evidence_rejected(change):
    member = _member()
    if change == "duplicate_role":
        member["ownership"]["characters"] *= 2
    elif change == "duplicate_effect":
        member["details"]["state_effects"] *= 2
    elif change == "outside_request":
        member["details"]["requested_codes"] = []
    elif change == "unknown_role":
        member["ownership"]["characters"].append({"name_code": "unknown_role"})
    else:
        member["ownership"]["characters"] = []
    with pytest.raises(ValueError):
        normalize_training(_capture(member), ROLES, FUNCTIONS)


def test_same_name_members_in_different_areas_have_distinct_identity():
    first, second = _member(), _member()
    second["area_id"] = "area_other"
    raw = capture_raw_training("union_demo", "2026-10-15T00:00:00Z", "reference_demo", (first, second))
    records = read_training(normalize_training(raw, ROLES, FUNCTIONS).training)
    assert len({r.member.key for r in records}) == 2
    with pytest.raises(ValueError):
        capture_raw_training("union_demo", "2026-10-15T00:00:00Z", "reference_demo", (first, first))


def test_missing_grade_does_not_fabricate_breakthrough_zero():
    member = _member()
    del member["ownership"]["characters"][0]["grade"]
    result = normalize_training(_capture(member), ROLES, FUNCTIONS)
    assert dict(read_training(result.training)[0].metrics)["breakthrough"] is None


def test_partial_details_cannot_assert_absent_traits_zero_even_with_coverage_flag():
    member = _member()
    member["details"]["status"] = "partial"
    result = normalize_training(_capture(member), ROLES, FUNCTIONS)
    assert all(value is None for _, value in read_training(result.training)[0].traits)


def test_numeric_option_whitelist_drops_unrelated_fields_and_rejects_nested_scalars():
    member = _member()
    detail = member["details"]["character_details"][0]
    detail["equip_option_password"] = "never-store"
    assert "never-store" not in _capture(member).serialized
    detail["skill1_lv"] = {"unexpected": "nested"}
    with pytest.raises(ValueError):
        _capture(member)


@pytest.mark.parametrize("value", [True, "1_0", "１０", "NaN", "", -1])
def test_invalid_skill_values_remain_unknown(value):
    member = _member()
    member["details"]["character_details"][0]["skill1_lv"] = value
    result = normalize_training(_capture(member), ROLES, FUNCTIONS)
    assert dict(read_training(result.training)[0].metrics)["skill_1"] is None


def test_change_tracked_function_reuses_same_raw_snapshot_without_refetch():
    normalized = normalize_training(_capture(_member()), ROLES, FUNCTIONS)
    member = MemberKey("area_demo", "member_demo")
    for function_id, expected in (("function_a", 12.34), ("function_b", 5.0)):
        rule = RoleRule("role_a", ("電擊",), "output", (MetricRule("trait_1", 1, 1, function_id),), 1)
        config = capture_config("common", "2026-10-15T00:00:00Z", "reference_demo", (rule,))
        assert metric_values(rule, read_training(normalized.training)[0])["trait_1"] == expected
        result = calculate_healthcheck(config, normalized.training, (member,), {member.key: "同名"}, "code_demo")
        assert result.snapshot_id == normalized.training.snapshot_id
    missing_rule = RoleRule("role_a", ("電擊",), "output", (MetricRule("trait_1", 1, 1, "new_function"),), 1)
    missing_config = capture_config("common", "2026-10-15T00:00:00Z", "reference_demo", (missing_rule,))
    with pytest.raises(IncompleteTraining):
        calculate_healthcheck(missing_config, normalized.training, (member,), {}, "code_demo")


def _capture(member):
    return capture_raw_training("union_demo", "2026-10-15T00:00:00Z", "reference_demo", (member,))


def _member():
    return {"area_id": "area_demo", "member_id": "member_demo",
        "ownership": {"status": "complete", "characters": [{"name_code": "role_a", "grade": 3, "core": 2}]},
        "details": {"status": "complete", "requested_codes": ["role_a"], "is_options_complete": True,
            "character_details": [{"name_code": "role_a", "skill1_lv": 10, "skill2_lv": 7, "ulti_skill_lv": 10,
                "head_equip_tier": 9, "head_equip_lv": 5, "arm_equip_tier": 0, "favorite_item_tid": "20001",
                "favorite_item_lv": 1, "equip_option_1": "1000101", "equip_option_2": "1000201"}],
            "state_effects": [{"id": "1000101", "function_details": [{"function_value": -1234}]},
                              {"id": "1000201", "function_details": [{"function_value": 500}]}]}}
