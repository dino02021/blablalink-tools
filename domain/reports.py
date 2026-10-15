"""Structured deficiencies and report text share one evaluation; no cell colors."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .healthcheck import ELEMENTS, Evaluation, RoleRule, evaluate_metric
from .snapshots import MemberKey, RoleLabel, TrainingRecord, metric_values
from .execution import check_execution


FENCE = "\x60" * 3
SKILLS = ("skill_1", "skill_2", "skill_burst")
EQUIPMENT = ("equip_head", "equip_arm", "equip_torso", "equip_leg")


@dataclass(frozen=True)
class RoleAssessment:
    name_code: str
    ownership_status: str
    metrics: tuple[Evaluation, ...]


@dataclass(frozen=True)
class ReportDraft:
    member: MemberKey
    assessments: tuple[RoleAssessment, ...]
    text: str
    warnings: tuple[str, ...]
    is_complete: bool


def assess_role(rule: RoleRule, record: TrainingRecord | None) -> RoleAssessment:
    if record is not None and record.name_code != rule.name_code:
        raise ValueError("record belongs to a different role")
    if record is None or record.ownership_status == "unknown":
        return RoleAssessment(rule.name_code, "unknown", ())
    if record.ownership_status == "not_owned":
        return RoleAssessment(rule.name_code, "not_owned", ())
    values = metric_values(rule, record)
    return RoleAssessment(rule.name_code, "owned", tuple(evaluate_metric(metric, values[metric.metric_id]) for metric in rule.metrics))


def build_report(member: MemberKey, display_name: str, rules: tuple[RoleRule, ...],
                 labels: tuple[RoleLabel, ...], records: tuple[TrainingRecord, ...]) -> ReportDraft:
    if not isinstance(display_name, str):
        raise ValueError("member display name must be a string")
    if len({rule.name_code for rule in rules}) != len(rules) or len({label.name_code for label in labels}) != len(labels):
        raise ValueError("duplicate report configuration")
    by_record = {}
    for record in records:
        check_execution()
        if record.member != member:
            continue
        if record.name_code in by_record:
            raise ValueError("duplicate training record")
        by_record[record.name_code] = record
    by_label = {label.name_code: label for label in labels}
    if by_label.keys() - {rule.name_code for rule in rules}:
        raise ValueError("unknown label target")
    sections: dict[str, list[str]] = {element: [] for element in ELEMENTS}
    assessments: list[RoleAssessment] = []
    warnings: list[str] = []
    for rule in rules:
        check_execution()
        label = by_label.get(rule.name_code, RoleLabel(rule.name_code, rule.name_code))
        if label.priority == "忽略":
            continue
        assessment = assess_role(rule, by_record.get(rule.name_code))
        assessments.append(assessment)
        if assessment.ownership_status == "unknown":
            warnings.append(f"{rule.name_code}: ownership_unknown")
            continue
        for metric in assessment.metrics:
            if metric.status == "unknown":
                warnings.append(f"{rule.name_code}/{metric.metric_id}: value_unknown")
        parts = _build_parts(rule, label, assessment)
        if not parts:
            continue
        prefix = f"【{_safe_label(label.priority)}】" if label.priority else ""
        line = f'{prefix}"{_safe_label(label.display_name or rule.name_code)}" {" | ".join(parts)}'
        for element in rule.elements:
            sections[element].append(line)
    body = "\n\n".join(f'# {element}\n' + "\n".join(sections[element]) for element in ELEMENTS if sections[element])
    text = f"@{_safe_label(display_name)}\n{FENCE}cs\n{body}\n{FENCE}" if body else ""
    return ReportDraft(member, tuple(assessments), text, tuple(warnings), not warnings)


def _build_parts(rule: RoleRule, label: RoleLabel, assessment: RoleAssessment) -> tuple[str, ...]:
    thresholds = {metric.metric_id: str(metric.threshold).strip() for metric in rule.metrics
                  if metric.threshold is not None and str(metric.threshold).strip()}
    failed = {metric.metric_id for metric in assessment.metrics if metric.status == "failed"}
    is_missing = assessment.ownership_status == "not_owned"
    if is_missing:
        failed = set(thresholds)
    parts = ["請抽取"] if is_missing else []
    for group, title in ((SKILLS, "技能"), (EQUIPMENT, "裝備")):
        if failed.intersection(group):
            parts.append(title + "/".join(thresholds.get(metric, "") for metric in group))
    if "collection" in failed:
        parts.append(thresholds["collection"])
    trait_labels = dict(label.trait_labels)
    for metric in rule.metrics:
        if metric.metric_id not in failed or metric.function_id is None:
            continue
        title = trait_labels.get(metric.function_id, metric.function_id)
        parts.append(_safe_label(title) + thresholds[metric.metric_id])
    # The legacy report does not render breakthrough-only failures.
    return tuple(dict.fromkeys(parts))


def _safe_label(value: str) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", " ", value).replace("\x60", "｀").replace('"', "＂").replace("@", "＠")
