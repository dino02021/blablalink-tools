"""Structured report generation with synthetic records, no Sheets colors."""

import pytest

from application.healthcheck import IncompleteTraining, calculate_healthcheck
from domain.healthcheck import MetricRule, RoleRule
from domain.reports import FENCE, build_report
from domain.snapshots import MemberKey, Override, RoleLabel, TrainingRecord, capture_config, capture_training


WHEN = "2026-10-15T00:00:00Z"
MEMBER = MemberKey("area_demo", "member_demo")
RULE = RoleRule("nikke_demo", ("電擊",), "output", (
    MetricRule("skill_1", 10, 1), MetricRule("skill_2", 7), MetricRule("skill_burst", 10),
    MetricRule("trait_1", 10, 1, "attack_demo"),
), 1)
LABEL = RoleLabel("nikke_demo", "角色暱稱", "優先", (("attack_demo", "攻擊"),))


def test_report_uses_same_failed_evaluations_and_full_group_thresholds():
    record = _record(skill_1=9, attack=5)
    result = build_report(MEMBER, "示範成員", (RULE,), (LABEL,), (record,))
    assert result.is_complete
    assert result.text == f'@示範成員\n{FENCE}cs\n# 電擊\n【優先】"角色暱稱" 技能10/7/10 | 攻擊10\n{FENCE}'
    assert [metric.metric_id for metric in result.assessments[0].metrics if metric.status == "failed"] == ["skill_1", "trait_1"]


def test_not_owned_and_missing_coverage_are_distinct():
    not_owned = TrainingRecord(MEMBER, "nikke_demo", "not_owned")
    report = build_report(MEMBER, "成員", (RULE,), (LABEL,), (not_owned,))
    assert report.is_complete
    assert "請抽取 | 技能10/7/10 | 攻擊10" in report.text
    for records in ((), (TrainingRecord(MEMBER, "nikke_demo", "unknown"),)):
        report = build_report(MEMBER, "成員", (RULE,), (LABEL,), records)
        assert not report.is_complete
        assert "請抽取" not in report.text
        assert "ownership_unknown" in report.warnings[0]


def test_missing_metric_is_incomplete_and_perfect_training_has_empty_report():
    partial = TrainingRecord(MEMBER, "nikke_demo", "owned", {"skill_1": 10}, {"attack_demo": 10})
    report = build_report(MEMBER, "成員", (RULE,), (LABEL,), (partial,))
    assert not report.is_complete
    assert any("skill_2" in warning for warning in report.warnings)
    perfect = build_report(MEMBER, "成員", (RULE,), (LABEL,), (_record(),))
    assert perfect.is_complete and perfect.text == ""


def test_ignored_role_does_not_require_data_or_emit_report():
    report = build_report(MEMBER, "成員", (RULE,), (RoleLabel("nikke_demo", "角色", "忽略"),), ())
    assert report.is_complete and not report.assessments and report.text == ""


def test_dual_element_report_order_does_not_depend_on_input_element_order():
    dual = RoleRule("dual_demo", ("鐵甲", "燃燒"), "output", (MetricRule("collection", "SR15"),))
    record = TrainingRecord(MEMBER, "dual_demo", "owned", {"collection": "R15"})
    report = build_report(MEMBER, "成員", (dual,), (), (record,))
    assert report.text.index("# 燃燒") < report.text.index("# 鐵甲")
    assert report.text.count('"dual_demo" SR15') == 2


def test_rendering_cannot_close_code_fence_or_inject_new_sections():
    malicious = FENCE + '\n@everyone"'
    report = build_report(MEMBER, malicious, (RULE,), (RoleLabel("nikke_demo", malicious),), (_record(skill_1=1),))
    assert report.text.count(FENCE) == 2
    assert "@everyone" not in report.text
    assert "＠everyone" in report.text


def test_snapshot_workflow_binds_config_data_overrides_and_code_versions():
    config = capture_config("common", WHEN, "reference_demo", (RULE,), (LABEL,))
    training = capture_training("union_demo", WHEN, "reference_demo", (_record(skill_1=9),))
    first = calculate_healthcheck(config, training, (MEMBER,), {MEMBER.key: "成員"}, "code_demo")
    same = calculate_healthcheck(config, training, (MEMBER,), {MEMBER.key: "成員"}, "code_demo")
    assert first == same and first.result_id == same.result_id
    assert first.config_id == config.snapshot_id and first.snapshot_id == training.snapshot_id
    second = calculate_healthcheck(config, training, (MEMBER,), {MEMBER.key: "成員"}, "code_demo_v2")
    assert first.result_id != second.result_id
    corrected = calculate_healthcheck(config, training, (MEMBER,), {MEMBER.key: "成員"}, "code_demo",
                                       (Override(MEMBER, "nikke_demo", "metric", "skill_1", 10),))
    assert corrected.reports[0].text == ""
    assert corrected.input_hash != first.input_hash
    assert first.reports[0].text


def test_incomplete_scoring_never_turns_unknown_into_zero():
    config = capture_config("common", WHEN, "reference_demo", (RULE,), (LABEL,))
    training = capture_training("union_demo", WHEN, "reference_demo", ())
    with pytest.raises(IncompleteTraining) as caught:
        calculate_healthcheck(config, training, (MEMBER,), {}, "code_demo")
    assert caught.value.missing
    not_owned = capture_training("union_demo", WHEN, "reference_demo", (TrainingRecord(MEMBER, "nikke_demo", "not_owned"),))
    result = calculate_healthcheck(config, not_owned, (MEMBER,), {}, "code_demo")
    assert result.scores.roles[0].value == 0
    assert "請抽取" in result.reports[0].text


def test_cross_scope_reference_and_roster_mismatches_rejected():
    config = capture_config("other_union", WHEN, "reference_demo", (RULE,))
    training = capture_training("union_demo", WHEN, "reference_demo", (_record(),))
    with pytest.raises(ValueError, match="scope"):
        calculate_healthcheck(config, training, (MEMBER,), {}, "code_demo")
    config = capture_config("common", WHEN, "other_reference", (RULE,))
    with pytest.raises(ValueError, match="reference"):
        calculate_healthcheck(config, training, (MEMBER,), {}, "code_demo")
    config = capture_config("common", WHEN, "reference_demo", (RULE,))
    with pytest.raises(ValueError, match="roster"):
        calculate_healthcheck(config, training, (MEMBER, MEMBER), {}, "code_demo")
    with pytest.raises(IncompleteTraining):
        calculate_healthcheck(config, training, (), {}, "code_demo")


def test_missing_scored_value_blocks_even_when_no_report_threshold():
    rule = RoleRule("nikke_demo", ("電擊",), "output", (MetricRule("skill_1", coefficient=1),), 1)
    config = capture_config("common", WHEN, "reference_demo", (rule,))
    training = capture_training("union_demo", WHEN, "reference_demo", (TrainingRecord(MEMBER, "nikke_demo", "owned"),))
    with pytest.raises(IncompleteTraining) as caught:
        calculate_healthcheck(config, training, (MEMBER,), {}, "code_demo")
    assert any("score_value_unknown" in item for item in caught.value.missing)


def test_untracked_ignored_role_does_not_require_coverage():
    rule = RoleRule("nikke_demo", ("電擊",), "output", (), is_score_tracked=False)
    config = capture_config("common", WHEN, "reference_demo", (rule,), (RoleLabel("nikke_demo", "角色", "忽略"),))
    training = capture_training("union_demo", WHEN, "reference_demo", ())
    result = calculate_healthcheck(config, training, (MEMBER,), {}, "code_demo")
    assert result.scores.roles == () and result.reports[0].text == ""


def test_breakthrough_remains_structured_even_when_legacy_text_omits_it():
    rule = RoleRule("nikke_demo", ("電擊",), "output", (MetricRule("breakthrough", 3),))
    record = TrainingRecord(MEMBER, "nikke_demo", "owned", {"breakthrough": 0})
    report = build_report(MEMBER, "成員", (rule,), (), (record,))
    assert report.assessments[0].metrics[0].status == "failed"
    assert report.is_complete and report.text == ""


def _record(skill_1=10, attack=10):
    return TrainingRecord(MEMBER, "nikke_demo", "owned",
                          {"skill_1": skill_1, "skill_2": 7, "skill_burst": 10}, {"attack_demo": attack})
