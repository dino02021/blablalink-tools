"""Pure training evaluation and full-cohort scoring, using synthetic/local inputs."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Mapping


ELEMENTS = ("電擊", "水冷", "燃燒", "風壓", "鐵甲")


@dataclass(frozen=True)
class MetricRule:
    metric_id: str
    threshold: str | int | float | None = None
    coefficient: str | int | float | None = None

    def __post_init__(self) -> None:
        if not self.metric_id.strip():
            raise ValueError("metric_id must be nonempty")
        if not _is_blank(self.threshold) and parse_number(self.threshold) is None:
            raise ValueError(f"{self.metric_id}: invalid threshold")
        if not _is_blank(self.coefficient) and parse_number(self.coefficient) is None:
            raise ValueError(f"{self.metric_id}: invalid coefficient")


@dataclass(frozen=True)
class RoleRule:
    name_code: str
    elements: tuple[str, ...]
    role_type: str
    metrics: tuple[MetricRule, ...]
    total_weight: float = 0
    is_score_tracked: bool = True

    def __post_init__(self) -> None:
        if not self.name_code.strip():
            raise ValueError("name_code must be nonempty")
        if not self.elements or len(set(self.elements)) != len(self.elements):
            raise ValueError("elements must be nonempty and unique")
        if any(element not in ELEMENTS for element in self.elements):
            raise ValueError("unknown element")
        if self.role_type not in ("output", "support"):
            raise ValueError("unknown role type")
        if len({rule.metric_id for rule in self.metrics}) != len(self.metrics):
            raise ValueError("duplicate metric_id")
        if isinstance(self.total_weight, bool) or not math.isfinite(self.total_weight):
            raise ValueError("total weight must be finite")


@dataclass(frozen=True)
class Evaluation:
    metric_id: str
    value: object
    threshold: object
    status: str
    comparison: str | None


@dataclass(frozen=True)
class Score:
    member_id: str
    key: str
    value: int
    rank: int | None = None


@dataclass(frozen=True)
class UnionScores:
    roles: tuple[Score, ...]
    groups: tuple[Score, ...]


def parse_number(raw: object) -> float | None:
    """Parse the GS numeric/grade subset, excluding bools and non-finite values."""
    if _is_blank(raw) or isinstance(raw, bool):
        return None
    if not isinstance(raw, (str, int, float)):
        return None
    text = re.sub(r"\s+", "", str(raw)).replace("：", ":")
    core = text[:-1] if text.endswith(("+", "-")) else text
    if core == "T9":
        return -1.0
    grade = re.fullmatch(r"(SR|R)(\d|1[0-5])", core)
    if grade:
        return float(int(grade[2]) + (17 if grade[1] == "SR" else 1))
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", core):
        return None
    try:
        number = float(core)
    except (ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def evaluate_metric(rule: MetricRule, value: object) -> Evaluation:
    if _is_blank(rule.threshold):
        return Evaluation(rule.metric_id, value, rule.threshold, "not_applicable", None)
    number = parse_number(value)
    if number is None:
        return Evaluation(rule.metric_id, value, rule.threshold, "unknown", None)
    threshold = parse_number(rule.threshold)
    text = str(rule.threshold).strip()
    comparison = "<=" if text.endswith("-") else ">="
    is_passed = number <= threshold if comparison == "<=" else number >= threshold
    return Evaluation(rule.metric_id, value, rule.threshold, "passed" if is_passed else "failed", comparison)


def competition_ranks(values: Mapping[str, int]) -> dict[str, int]:
    ordered = sorted(values.items(), key=lambda item: (-item[1], item[0]))
    ranks: dict[str, int] = {}
    previous: int | None = None
    rank = 0
    for position, (member_id, value) in enumerate(ordered, 1):
        if previous is None or value != previous:
            rank = position
        ranks[member_id] = rank
        previous = value
    return ranks


def score_union(
    member_ids: tuple[str, ...],
    rules: tuple[RoleRule, ...],
    values: Mapping[tuple[str, str], Mapping[str, object]],
) -> UnionScores:
    """Recompute all valid members; unrelated data never changes the maxima."""
    if len(set(member_ids)) != len(member_ids) or any(not member for member in member_ids):
        raise ValueError("member IDs must be nonempty and unique")
    if len({rule.name_code for rule in rules}) != len(rules):
        raise ValueError("duplicate name_code")
    active_rules = tuple(rule for rule in rules if rule.is_score_tracked)
    by_role: dict[str, dict[str, int]] = {}
    role_scores: list[Score] = []
    for rule in active_rules:
        raw_by_member = {}
        for member_id in member_ids:
            row = values.get((member_id, rule.name_code), {})
            raw = 0.0
            for metric in rule.metrics:
                number = parse_number(row.get(metric.metric_id))
                coefficient = parse_number(metric.coefficient)
                if number is not None and coefficient is not None:
                    raw += number * coefficient
            if not math.isfinite(raw):
                raise ValueError("score arithmetic overflow")
            raw_by_member[member_id] = raw
        scores = _normalize(raw_by_member)
        by_role[rule.name_code] = scores
        role_scores.extend(Score(member_id, rule.name_code, value) for member_id, value in scores.items())
    groups: list[Score] = []
    for group in (*ELEMENTS, "輔助"):
        eligible = tuple(rule for rule in active_rules if _is_in_group(rule, group) and rule.total_weight > 0)
        if not eligible:
            continue
        totals = {}
        for member_id in member_ids:
            raw_total = sum(by_role[rule.name_code][member_id] * rule.total_weight for rule in eligible)
            if not math.isfinite(raw_total):
                raise ValueError("total arithmetic overflow")
            totals[member_id] = math.floor(raw_total + 0.5)
        normalized = _normalize(totals)
        ranks = competition_ranks(normalized)
        groups.extend(Score(member_id, group, value, ranks[member_id]) for member_id, value in normalized.items())
    return UnionScores(tuple(role_scores), tuple(groups))


def _is_blank(value: object) -> bool:
    return value is None or isinstance(value, str) and not value.strip()


def _is_in_group(rule: RoleRule, group: str) -> bool:
    if group == "輔助":
        return rule.role_type == "support"
    return rule.role_type == "output" and group in rule.elements


def _normalize(values: Mapping[str, float]) -> dict[str, int]:
    maximum = max((0.0, *values.values()))
    if maximum <= 0:
        return {key: 0 for key in values}
    return {key: math.floor(min(100.0, max(0.0, value / maximum * 100))) for key, value in values.items()}
