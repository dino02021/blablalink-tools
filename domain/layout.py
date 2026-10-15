"""Stable physical-column planning and typed cell rendering, independent of Sheets."""

from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass, replace

from domain.snapshots import canonical_json


@dataclass(frozen=True)
class Column:
    column_id: str
    label: str
    owner: str = "system"
    format_kind: str = "text"
    is_archived: bool = False

    def __post_init__(self):
        if not isinstance(self.column_id, str) or not self.column_id.strip():
            raise ValueError("column requires stable identity")
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("column label required")
        if self.owner not in ("system", "human") or self.format_kind not in ("text", "number", "percent"):
            raise ValueError("invalid column ownership or format")
        if type(self.is_archived) is not bool or (self.owner == "human" and self.is_archived):
            raise ValueError("invalid archive state")


@dataclass(frozen=True)
class Layout:
    resource: str
    tab_id: int
    columns: tuple[Column, ...]
    schema_version: int = 1

    def __post_init__(self):
        if not isinstance(self.resource, str) or not self.resource.strip():
            raise ValueError("workbook required")
        if type(self.tab_id) is not int or self.tab_id < 0 or type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("invalid layout schema or tab ID")
        columns = tuple(self.columns)
        if not columns or any(not isinstance(c, Column) for c in columns):
            raise ValueError("columns required")
        if len({c.column_id for c in columns}) != len(columns):
            raise ValueError("duplicate column identity")
        object.__setattr__(self, "columns", columns)

    @property
    def revision(self) -> str:
        return _hash(asdict(self))


@dataclass(frozen=True)
class LayoutPlan:
    before: Layout
    after: Layout
    config_id: str
    code_sha: str
    changes: tuple[tuple[str, str, int], ...]

    @property
    def plan_id(self) -> str:
        return _hash(asdict(self))


def prepare_layout(current: Layout, desired: tuple[Column, ...], config_id: str, code_sha: str) -> LayoutPlan:
    """Keep physical identity; archive removed system columns, append newly required ones."""
    desired = tuple(desired)
    if not desired or any(not isinstance(c, Column) or c.owner != "system" or c.is_archived for c in desired):
        raise ValueError("desired layout must contain active system columns")
    if len({c.column_id for c in desired}) != len(desired):
        raise ValueError("duplicate desired identity")
    if any(not isinstance(v, str) or not v.strip() for v in (config_id, code_sha)):
        raise ValueError("configuration and code versions required")
    wanted = {c.column_id: c for c in desired}
    columns, changes = [], []
    for index, existing in enumerate(current.columns):
        target = wanted.pop(existing.column_id, None)
        if existing.owner == "human":
            if target:
                raise ValueError("system column collides with human identity")
            columns.append(existing)
            continue
        updated = target if target else replace(existing, is_archived=True)
        columns.append(updated)
        if updated != existing:
            action = "archive" if updated.is_archived else "restore" if existing.is_archived else "update"
            changes.append((action, existing.column_id, index))
    for target in wanted.values():
        changes.append(("append", target.column_id, len(columns)))
        columns.append(target)
    after = Layout(current.resource, current.tab_id, tuple(columns))
    return LayoutPlan(current, after, config_id, code_sha, tuple(changes))


def confirm_layout(plan: LayoutPlan, current: Layout, confirmed_plan_id: str,
                   config_id: str, code_sha: str) -> Layout:
    if plan.plan_id != confirmed_plan_id or current != plan.before:
        raise ValueError("layout confirmation or base revision differs")
    if config_id != plan.config_id or code_sha != plan.code_sha:
        raise ValueError("layout dependency changed")
    # Rebuild the plan to reject manually forged plans that could alter human columns.
    desired = tuple(c for c in plan.after.columns if c.owner == "system" and not c.is_archived)
    if prepare_layout(current, desired, config_id, code_sha) != plan:
        raise ValueError("invalid layout operations")
    return plan.after


def render_columns(layout: Layout, member_ids: tuple[str, ...], values: dict,
                   statuses: dict | None = None, previous_member_count: int = 0,
                   existing_member_ids: tuple[str, ...] | None = None) -> tuple[dict, ...]:
    """Emit explicit system-column ranges only; strings are never parsed as formulas."""
    member_ids = tuple(member_ids)
    if type(previous_member_count) is not int or previous_member_count < 0:
        raise ValueError("invalid previous row count")
    if any(c.owner == "human" for c in layout.columns) and tuple(existing_member_ids or ()) != member_ids:
        raise ValueError("mixed human columns require unchanged member row identities")
    if (not member_ids or len(set(member_ids)) != len(member_ids)
            or any(not isinstance(m, str) or not m.strip() for m in member_ids)):
        raise ValueError("unique nonempty member identities required")
    active = {c.column_id for c in layout.columns if c.owner == "system" and not c.is_archived}
    expected = {(member, column) for member in member_ids for column in active}
    if set(values) != expected or (statuses is not None and not set(statuses) <= expected):
        raise ValueError("render input coverage differs")
    requests = []
    for index, column in enumerate(layout.columns):
        if column.owner != "system" or column.is_archived:
            continue
        cells = [{"userEnteredValue": {"stringValue": column.label}}]
        for member in member_ids:
            key = (member, column.column_id)
            cell = {"userEnteredValue": _cell_value(values[key]),
                    "userEnteredFormat": {"numberFormat": {"type": {"text": "TEXT", "number": "NUMBER", "percent": "PERCENT"}[column.format_kind]}}}
            status = (statuses or {}).get(key, "not_applicable")
            if status not in ("pass", "fail", "unknown", "not_applicable"):
                raise ValueError("invalid assessment status")
            cell["note"] = status
            cell["userEnteredFormat"]["textFormat"] = {"foregroundColor": {
                "fail": {"red": 1, "green": 0, "blue": 0},
                "unknown": {"red": 0.6, "green": 0.4, "blue": 0},
            }.get(status, {"red": 0, "green": 0, "blue": 0})}
            cells.append(cell)
        for _ in range(max(0, previous_member_count - len(member_ids))):
            cells.append({"userEnteredValue": {}, "userEnteredFormat": {}, "note": ""})
        requests.append({"updateCells": {"range": {"sheetId": layout.tab_id, "startRowIndex": 0,
            "endRowIndex": len(cells), "startColumnIndex": index, "endColumnIndex": index + 1},
            "rows": [{"values": [cell]} for cell in cells], "fields": "userEnteredValue,userEnteredFormat,note"}})
    return tuple(requests)


def _cell_value(value) -> dict:
    if value is None:
        return {}
    if type(value) is bool:
        return {"boolValue": value}
    if isinstance(value, str):
        return {"stringValue": value}
    if type(value) in (int, float):
        if type(value) is int and abs(value) > 2 ** 53:
            raise ValueError("integer exceeds exact binary64 range; use an identity string")
        if math.isfinite(value):
            return {"numberValue": value}
    raise ValueError("unsupported cell value")


def _hash(value) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()
