"""Structured mock standard table reader; no live Sheets API or legacy settings."""

from dataclasses import asdict, dataclass, replace

from domain.healthcheck import MetricRule, RoleRule
from domain.snapshots import RoleLabel, canonical_json, capture_config


@dataclass(frozen=True)
class StandardReference:
    version: str
    role_ids: tuple[str, ...]
    function_ids: tuple[str, ...]

    def __post_init__(self):
        for field in ("role_ids", "function_ids"):
            if isinstance(getattr(self, field), str):
                raise ValueError("reference identities must be a sequence")
            values = tuple(getattr(self, field))
            if len(set(values)) != len(values) or any(not isinstance(v, str) or not v.strip() for v in values):
                raise ValueError("unique stable reference identities required")
            object.__setattr__(self, field, values)
        if not self.role_ids or not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("pinned reference required")


def read_standard_sheet(book: dict, scope_id: str, captured_at: str, reference: StandardReference):
    rows = book["inputs"].get("standard_rows")
    if not isinstance(rows, list):
        raise ValueError("explicit standard_rows table required")
    rules, labels = [], []
    allowed = {"name_code", "display_name", "elements", "role_type", "tracking", "total_weight", "priority", "metrics", "trait_labels"}
    for row in rows:
        if row is None or row == {}:
            continue
        if not isinstance(row, dict) or set(row) - allowed:
            raise ValueError("unknown or invalid standard columns")
        if row.get("name_code") not in reference.role_ids:
            raise ValueError("standard role outside pinned reference")
        raw_metrics = row.get("metrics", [])
        if (not isinstance(raw_metrics, list) or any(not isinstance(m, dict) or "metric_id" not in m
                or set(m) - {"metric_id", "threshold", "coefficient", "function_id"} for m in raw_metrics)):
            raise ValueError("explicit supported metric columns required")
        metrics = tuple(MetricRule(**m) for m in raw_metrics)
        if any(m.function_id is not None and m.function_id not in reference.function_ids for m in metrics):
            raise ValueError("standard function outside pinned reference")
        tracking = row.get("tracking", "")
        if not isinstance(tracking, str):
            raise ValueError("tracking must be an explicit sheet label")
        weight = row.get("total_weight")
        if weight in (None, ""):
            weight = 0
        if type(weight) not in (int, float):
            raise ValueError("structured total_weight must be numeric")
        rules.append(RoleRule(row["name_code"], row["elements"], row["role_type"], metrics, weight, tracking == "追蹤"))
        labels.append(RoleLabel(row["name_code"], row.get("display_name", row["name_code"]), row.get("priority", ""), row.get("trait_labels", {})))
    if not rules:
        raise ValueError("empty standard table cannot replace a published configuration")
    combined, combined_labels = {}, {}
    seen_pairs = set()
    for rule, label in zip(rules, labels):
        pair = (rule.name_code, rule.role_type)
        if pair in seen_pairs:
            raise ValueError("duplicate role and type")
        seen_pairs.add(pair)
        previous = combined.get(rule.name_code)
        if previous:
            old_data, new_data = asdict(previous), asdict(rule)
            for key in ("role_type", "is_score_tracked", "additional_role_types", "tracked_role_types"):
                old_data.pop(key)
                new_data.pop(key)
            if canonical_json(old_data) != canonical_json(new_data) or combined_labels[rule.name_code] != label:
                raise ValueError("same role types disagree on shared standard or display metadata")
            declared = tuple(t for t in ("output", "support") if t in (previous.role_type, *previous.additional_role_types, rule.role_type))
            tracked = tuple(t for t in declared if t in (*previous.tracked_types, *rule.tracked_types))
            rule = replace(previous, role_type=declared[0], additional_role_types=declared[1:],
                           tracked_role_types=tracked, is_score_tracked=bool(tracked))
        combined[rule.name_code] = rule
        combined_labels[rule.name_code] = label
    return capture_config(scope_id, captured_at, reference.version, tuple(combined.values()), tuple(combined_labels.values()))
