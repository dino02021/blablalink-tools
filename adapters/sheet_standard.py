"""Structured mock standard table reader; no live Sheets API or legacy settings."""

from dataclasses import dataclass

from domain.healthcheck import MetricRule, RoleRule
from domain.snapshots import RoleLabel, capture_config


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
    return capture_config(scope_id, captured_at, reference.version, tuple(rules), tuple(labels))
