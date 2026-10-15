"""Synthetic identities and numeric fixtures only; never read real workbooks."""

import socket

import pytest

from domain.healthcheck import MetricRule, RoleRule, competition_ranks, evaluate_metric, parse_number, score_union


@pytest.mark.parametrize(
    "raw,expected",
    [("T9", -1), ("R0", 1), ("R15", 16), ("SR0", 17), ("SR15", 32),
     ("SR15+", 32), (" 1 0 + ", 10), ("1e-2", .01), (0, 0),
     ("", None), (None, None), (False, None), ("SR16", None), ("R01", None),
     ("Infinity", None), (float("nan"), None), ([], None), ("1e999", None)],
)
def test_grade_and_number_boundaries(raw, expected):
    assert parse_number(raw) == expected


@pytest.mark.parametrize(
    "threshold,value,status,comparison",
    [("10+", 9, "failed", ">="), ("10+", 10, "passed", ">="),
     ("10-", 10, "passed", "<="), ("10-", 11, "failed", "<="),
     ("R0", "T9", "failed", ">="), ("SR0", "R15", "failed", ">="),
     (0, 0, "passed", ">="), ("", None, "not_applicable", None),
     (10, None, "unknown", None), (10, "invalid", "unknown", None)],
)
def test_threshold_boundaries(threshold, value, status, comparison):
    result = evaluate_metric(MetricRule("skill_1", threshold), value)
    assert (result.status, result.comparison) == (status, comparison)


@pytest.mark.parametrize("invalid", ["broken", "NaN", float("inf"), True])
def test_invalid_published_rules_are_rejected(invalid):
    with pytest.raises(ValueError):
        MetricRule("skill_1", invalid)


def test_highest_member_changes_everyone_and_ignores_outside_roster():
    rules = (RoleRule("nikke_demo", ("電擊",), "output", (MetricRule("skill_1", coefficient=1),), 1),)
    data = {("a", "nikke_demo"): {"skill_1": 10}, ("b", "nikke_demo"): {"skill_1": 5},
            ("not_a_member", "nikke_demo"): {"skill_1": 10000}}
    result = score_union(("a", "b"), rules, data)
    assert [(row.member_id, row.value) for row in result.roles] == [("a", 100), ("b", 50)]
    data[("b", "nikke_demo")]["skill_1"] = 20
    result = score_union(("a", "b"), rules, data)
    assert [(row.member_id, row.value) for row in result.roles] == [("a", 50), ("b", 100)]
    assert [(row.member_id, row.rank) for row in result.groups] == [("a", 2), ("b", 1)]


def test_total_is_js_rounded_before_normalization_not_python_bankers_rounding():
    rules = (RoleRule("nikke_demo", ("水冷",), "output", (MetricRule("skill_1", coefficient=1),), .5),)
    result = score_union(("a", "b"), rules,
                         {("a", "nikke_demo"): {"skill_1": 100}, ("b", "nikke_demo"): {"skill_1": 1}})
    assert [(row.value, row.rank) for row in result.groups] == [(100, 1), (2, 2)]


def test_dual_element_and_support_are_independent_groups():
    rules = (
        RoleRule("dual_demo", ("燃燒", "鐵甲"), "output", (MetricRule("x", coefficient=1),), 1),
        RoleRule("support_demo", ("燃燒",), "support", (MetricRule("x", coefficient=1),), 1),
        RoleRule("ignored_demo", ("電擊",), "output", (), 1, is_score_tracked=False),
    )
    result = score_union(("a",), rules, {("a", "dual_demo"): {"x": 5}, ("a", "support_demo"): {"x": 3}})
    assert [(row.key, row.value) for row in result.groups] == [("燃燒", 100), ("鐵甲", 100), ("輔助", 100)]
    assert len(result.roles) == 2


def test_missing_nonpositive_and_unweighted_roles():
    rules = (
        RoleRule("a", ("風壓",), "output", (MetricRule("x", coefficient=-1),), 1),
        RoleRule("b", ("鐵甲",), "output", (), 0),
    )
    result = score_union(("member_demo",), rules, {("member_demo", "a"): {"x": 2}})
    assert all(row.value == 0 for row in (*result.roles, *result.groups))
    assert [row.key for row in result.groups] == ["風壓"]


def test_competition_ranking_is_deterministic():
    assert competition_ranks({"b": 100, "c": 90, "a": 100, "d": 0}) == {"a": 1, "b": 1, "c": 3, "d": 4}


def test_duplicate_members_and_roles_fail_instead_of_merging():
    with pytest.raises(ValueError):
        score_union(("a", "a"), (), {})
    rule = RoleRule("demo", ("電擊",), "output", ())
    with pytest.raises(ValueError):
        score_union(("a",), (rule, rule), {})


def test_network_guard_is_active():
    with pytest.raises(AssertionError, match="Network is forbidden"):
        socket.create_connection(("production.invalid", 443))
