"""Snapshot-bound offline calculation; refuses incomplete cohorts before scoring."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Mapping

from domain.healthcheck import UnionScores, parse_score_number, score_union
from domain.reports import ReportDraft, build_report
from domain.snapshots import (
    Document, MemberKey, Override, apply_overrides, canonical_json,
    metric_values, read_config, read_training,
)


class IncompleteTraining(ValueError):
    def __init__(self, missing: tuple[str, ...]) -> None:
        super().__init__("incomplete training coverage")
        self.missing = missing


@dataclass(frozen=True)
class HealthcheckResult:
    config_id: str
    snapshot_id: str
    code_version: str
    input_hash: str
    scores: UnionScores
    reports: tuple[ReportDraft, ...]

    @property
    def result_id(self) -> str:
        return hashlib.sha256(canonical_json(asdict(self)).encode("utf-8")).hexdigest()


def calculate_healthcheck(
    config: Document,
    training: Document,
    members: tuple[MemberKey, ...],
    display_names: Mapping[str, str],
    code_version: str,
    overrides: tuple[Override, ...] = (),
) -> HealthcheckResult:
    if not isinstance(code_version, str) or not code_version.strip():
        raise ValueError("code version is required")
    if config.envelope["scope_id"] not in ("common", training.envelope["scope_id"]):
        raise ValueError("configuration scope mismatch")
    if config.envelope["reference_version"] != training.envelope["reference_version"]:
        raise ValueError("reference version mismatch")
    if any(not isinstance(member, MemberKey) for member in members) or len(set(members)) != len(members):
        raise ValueError("invalid or duplicate roster member")
    if not members:
        raise IncompleteTraining(("roster: missing_or_empty",))
    rules, labels = read_config(config)
    records = apply_overrides(read_training(training), overrides)
    indexed = {record.key: record for record in records}
    roster_keys = {member.key for member in members}
    role_codes = {rule.name_code for rule in rules}
    if any(override.member.key not in roster_keys or override.name_code not in role_codes for override in overrides):
        raise ValueError("override outside current roster or configuration")
    missing: list[str] = []
    values = {}
    for member in members:
        for rule in rules:
            if not rule.is_score_tracked:
                continue
            record = indexed.get((member.key, rule.name_code))
            if record is None or record.ownership_status == "unknown":
                missing.append(f"{member.key}/{rule.name_code}: ownership_unknown")
                continue
            if record.ownership_status == "not_owned":
                values[(member.key, rule.name_code)] = {}
                continue
            row = metric_values(rule, record)
            for metric in rule.metrics:
                coefficient = parse_score_number(metric.coefficient)
                if coefficient is not None and coefficient != 0 and parse_score_number(row[metric.metric_id]) is None:
                    missing.append(f"{member.key}/{rule.name_code}/{metric.metric_id}: score_value_unknown")
            values[(member.key, rule.name_code)] = row
    reports = tuple(build_report(member, display_names.get(member.key, member.member_id), rules, labels, records) for member in members)
    for report in reports:
        missing.extend(f"{report.member.key}/{warning}" for warning in report.warnings)
    if missing:
        raise IncompleteTraining(tuple(dict.fromkeys(missing)))
    input_hash = hashlib.sha256(canonical_json({
        "members": [asdict(member) for member in members],
        "display_names": {member.key: display_names.get(member.key, member.member_id) for member in members},
        "overrides": [asdict(override) for override in sorted(overrides, key=lambda item: item.key)],
    }).encode("utf-8")).hexdigest()
    return HealthcheckResult(config.snapshot_id, training.snapshot_id, code_version, input_hash,
                             score_union(tuple(member.key for member in members), rules, values), reports)
