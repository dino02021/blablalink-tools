"""Typed, bounded generated grid models. No Sheets API or formula evaluation."""

from dataclasses import asdict, dataclass
import hashlib

from domain.layout import encode_cell_value
from domain.snapshots import canonical_json

STYLES = frozenset({"header", "identity", "standard", "coefficient", "weight", "data", "passed", "failed", "unknown", "not_owned"})


@dataclass(frozen=True)
class ViewCell:
    value: object = None
    style: str = "data"
    note: str = ""
    format_kind: str = "text"

    def __post_init__(self):
        encode_cell_value(self.value)
        if self.style not in STYLES or self.format_kind not in ("text", "number") or not isinstance(self.note, str):
            raise ValueError("invalid view cell presentation")


@dataclass(frozen=True)
class ViewTab:
    tab_key: str
    title: str
    column_ids: tuple[str, ...]
    row_ids: tuple[str, ...]
    rows: tuple[tuple[ViewCell, ...], ...]
    merges: tuple[tuple[int, int, int, int], ...] = ()
    frozen_rows: int = 0
    frozen_columns: int = 0
    hidden_columns: tuple[int, ...] = ()
    is_generated_protected: bool = True

    def __post_init__(self):
        columns, identities = tuple(self.column_ids), tuple(self.row_ids)
        rows, merges = tuple(tuple(row) for row in self.rows), tuple(tuple(m) for m in self.merges)
        if (not isinstance(self.tab_key, str) or not self.tab_key.strip() or not isinstance(self.title, str) or not self.title.strip()
                or not 1 <= len(columns) <= 10000 or not 1 <= len(rows) <= 10000
                or len(rows) * len(columns) > 500000 or len(identities) != len(rows)
                or len(set(columns)) != len(columns) or len(set(identities)) != len(identities)
                or any(not isinstance(v, str) or not v for v in (*columns, *identities))
                or any(len(row) != len(columns) or any(not isinstance(cell, ViewCell) for cell in row) for row in rows)):
            raise ValueError("invalid bounded rectangular view")
        if (type(self.frozen_rows) is not int or not 0 <= self.frozen_rows <= len(rows)
                or type(self.frozen_columns) is not int or not 0 <= self.frozen_columns <= len(columns)
                or self.is_generated_protected is not True):
            raise ValueError("generated view ownership or frozen dimensions differ")
        hidden = tuple(self.hidden_columns)
        if len(set(hidden)) != len(hidden) or any(type(c) is not int or not 0 <= c < len(columns) for c in hidden):
            raise ValueError("invalid hidden identity columns")
        occupied = set()
        for merge in merges:
            if len(merge) != 4 or any(type(v) is not int for v in merge):
                raise ValueError("invalid merge coordinates")
            r0, r1, c0, c1 = merge
            if not (0 <= r0 < r1 <= len(rows) and 0 <= c0 < c1 <= len(columns)) or (r1-r0)*(c1-c0) <= 1:
                raise ValueError("merge is outside view")
            positions = {(r, c) for r in range(r0, r1) for c in range(c0, c1)}
            if positions & occupied or any(rows[r][c].value is not None for r, c in positions - {(r0, c0)}):
                raise ValueError("merge overlaps or hides nonempty data")
            occupied.update(positions)
        for name, value in (("column_ids", columns), ("row_ids", identities), ("rows", rows), ("merges", merges), ("hidden_columns", hidden)):
            object.__setattr__(self, name, value)

    @property
    def layout_revision(self):
        return hashlib.sha256(canonical_json({"tab_key": self.tab_key, "columns": self.column_ids,
            "merges": self.merges, "frozen_rows": self.frozen_rows, "frozen_columns": self.frozen_columns,
            "hidden_columns": self.hidden_columns}).encode()).hexdigest()

    def typed_rows(self):
        return tuple(tuple({"value": encode_cell_value(cell.value), "style": cell.style,
            "note": cell.note, "format_kind": cell.format_kind, "foreground_hex": {
                "header": "#FFFFFF", "failed": "#D93025", "unknown": "#9C6500", "not_owned": "#808080"
            }.get(cell.style, "#000000"), "background_hex": {
                "header": "#243B53", "standard": "#E8F0FE", "coefficient": "#FFF2CC", "weight": "#D9EAD3"
            }.get(cell.style, "#FFFFFF")} for cell in row) for row in self.rows)


@dataclass(frozen=True)
class WorkbookView:
    config_id: str
    training_id: str
    calculation_id: str
    code_sha: str
    tabs: tuple[ViewTab, ...]
    schema_version: int = 1

    def __post_init__(self):
        tabs = tuple(self.tabs)
        if (type(self.schema_version) is not int or self.schema_version != 1 or not tabs
                or any(not isinstance(tab, ViewTab) for tab in tabs) or len({t.tab_key for t in tabs}) != len(tabs)
                or len({t.title for t in tabs}) != len(tabs) or any(not isinstance(v, str) or not v for v in
                    (self.config_id, self.training_id, self.calculation_id, self.code_sha))
                or sum(len(t.rows)*len(t.column_ids) for t in tabs) > 500000):
            raise ValueError("invalid bounded workbook view")
        object.__setattr__(self, "tabs", tabs)

    @property
    def view_id(self):
        return hashlib.sha256(canonical_json(asdict(self)).encode()).hexdigest()


def restore_workbook_view(data: dict) -> WorkbookView:
    tabs = tuple(ViewTab(**{**tab, "rows": tuple(tuple(ViewCell(**cell) for cell in row) for row in tab["rows"])}) for tab in data["tabs"])
    return WorkbookView(**{**data, "tabs": tabs})
