"""Explicit, bounded eleven-to-twelve-column mock standard draft migration."""

import copy
from dataclasses import asdict, dataclass
import hashlib
import json

from adapters.legacy_standard import LegacyCatalog, read_legacy_standard
from domain.layout import encode_cell_value
from domain.snapshots import canonical_json
from domain.standard_layout import METRICS

SOURCE_KEYS = ("legacy_checklist", "legacy_standard", "standard_column_count", "standard_role_ids", "standard_merges", "standard_cell_notes", "standard_column_ids")


@dataclass(frozen=True)
class LegacyMigrationPlan:
    resource: str
    layout_revision: str
    scope_id: str
    captured_at: str
    code_sha: str
    catalog_hash: str
    source_hash: str
    config_id: str
    target_inputs_json: str
    column_map: tuple[tuple[int, int], ...]
    inserted_columns: tuple[int, ...]

    @property
    def plan_id(self) -> str:
        return _hash(asdict(self))

    @property
    def target_layout_revision(self) -> str:
        return _hash({"before": self.layout_revision, "migration": self.plan_id})


def prepare_legacy_migration(resource: str, book: dict, catalog: LegacyCatalog, scope_id: str,
                             captured_at: str, code_sha: str) -> LegacyMigrationPlan:
    if any(not isinstance(v, str) or not v.strip() for v in (resource, scope_id, captured_at, code_sha, book["layout_revision"])):
        raise ValueError("explicit migration dependencies required")
    inputs = book["inputs"]
    source = {key: inputs[key] for key in SOURCE_KEYS if key in inputs}
    if len(canonical_json(source).encode()) > 8 * 1024 * 1024:
        raise ValueError("legacy migration source exceeds bound")
    grid, width = inputs["legacy_standard"], inputs["standard_column_count"]
    if (type(width) is not int or not 12 <= width <= 9000 or (width-1) % 11
            or not isinstance(grid, list) or len(grid) != 6 or any(not isinstance(row, list) or len(row) != width for row in grid)):
        raise ValueError("explicit six-row eleven-column source required")
    starts = tuple(range(1, width, 11))
    if [i for i, cell in enumerate(grid[1]) if i and cell not in (None, "")] != list(starts):
        raise ValueError("legacy role blocks are not aligned")
    identities = inputs.get("standard_role_ids")
    if (not isinstance(identities, dict) or set(identities) != {str(start+1) for start in starts}
            or any(value not in catalog.reference.role_ids for value in identities.values())):
        raise ValueError("every legacy block requires a protected role identity")
    for row in grid:
        for cell in row:
            encode_cell_value(cell)
    column_map = ((0, 0), *tuple((column, column+1+(column-1)//11) for column in range(1, width)))
    mapping = dict(column_map)
    inserted = tuple(1+12*i for i in range(len(starts)))
    target = [[None] * (1+12*len(starts)) for _ in range(6)]
    for row in range(6):
        for old, new in column_map:
            target[row][new] = copy.deepcopy(grid[row][old])
    notes = _read_notes(inputs.get("standard_cell_notes"), width)
    target_notes = {(row, mapping[column]): note for (row, column), note in notes.items()}
    for start, new in zip(starts, inserted):
        for row in (0, 1, 5):
            target[row][new], target[row][new+1] = target[row][new+1], None
            note = target_notes.pop((row, new+1), None)
            if note is not None:
                target_notes[(row, new)] = note
        target[2][new] = "突破"
    merges = _convert_merges(inputs.get("standard_merges"), grid, notes, starts, mapping)
    after = {**source, "legacy_standard": target, "standard_column_count": len(target[0]),
        "standard_role_ids": {str(new+1): identities[str(old+1)] for old, new in zip(starts, inserted)},
        "standard_merges": merges, "standard_cell_notes": {f"{r},{c}": note for (r, c), note in sorted(target_notes.items())}}
    column_ids, source_ids, owner = ["row_label"], ["row_label"], None
    for old in starts:
        if grid[0][old] not in (None, ""):
            owner = grid[0][old]
        column_ids.extend(canonical_json([owner, identities[str(old+1)], metric]) for metric in (*METRICS, "trait_1", "trait_2", "trait_3"))
        source_ids.extend(canonical_json([owner, identities[str(old+1)], metric]) for metric in (*METRICS[1:], "trait_1", "trait_2", "trait_3"))
    if len(set(column_ids)) != len(column_ids):
        raise ValueError("duplicate standard column identities")
    if "standard_column_ids" in inputs and inputs["standard_column_ids"] != source_ids:
        raise ValueError("protected source column identities differ")
    after["standard_column_ids"] = column_ids
    document = read_legacy_standard({"inputs": after}, scope_id, captured_at, catalog)
    return LegacyMigrationPlan(resource, book["layout_revision"], scope_id, captured_at, code_sha, _hash(asdict(catalog)),
        _hash(source), document.snapshot_id, canonical_json(after), tuple(column_map), inserted)


def confirm_legacy_migration(plan: LegacyMigrationPlan, book: dict, catalog: LegacyCatalog,
                             confirmed_plan_id: str, config_id: str, code_sha: str) -> dict:
    current = prepare_legacy_migration(plan.resource, book, catalog, plan.scope_id, plan.captured_at, code_sha)
    if current != plan or confirmed_plan_id != plan.plan_id or config_id != plan.config_id:
        raise ValueError("legacy migration confirmation or source changed")
    return json.loads(plan.target_inputs_json)


def _read_notes(data, width):
    if not isinstance(data, dict):
        raise ValueError("explicit legacy cell note map required")
    notes = {}
    for key, value in data.items():
        try:
            row, column = (int(part) for part in key.split(","))
        except (ValueError, AttributeError):
            raise ValueError("invalid legacy note coordinate") from None
        if key != f"{row},{column}" or not 0 <= row < 6 or not 0 <= column < width or not isinstance(value, str):
            raise ValueError("invalid legacy note coordinate or text")
        notes[(row, column)] = value
    return notes


def _convert_merges(merges, grid, notes, starts, mapping):
    if not isinstance(merges, list):
        raise ValueError("explicit legacy merge inventory required")
    result, occupied = [], set()
    for merge in merges:
        if not isinstance(merge, (list, tuple)) or len(merge) != 4 or any(type(v) is not int for v in merge):
            raise ValueError("invalid legacy merge")
        r0, r1, c0, c1 = merge
        if not (0 <= r0 < r1 <= 6 and r1 == r0+1 and 1 <= c0 < c1 <= len(grid[0])):
            raise ValueError("legacy merge is outside template")
        block = next((start for start in starts if start <= c0 < start+11), None)
        is_header = r0 in (0, 1, 5) and c0 in starts and (c1-1) % 11 == 0
        if is_header and r0 in (1, 5) and c1 != c0+11:
            raise ValueError("legacy role or weight merge crosses blocks")
        if not is_header and not (r0 == 2 and (c0-block, c1-block) in ((0, 3), (3, 7), (8, 11))):
            raise ValueError("unsupported legacy merge shape")
        positions = {(r0, column) for column in range(c0, c1)}
        if positions & occupied or any(grid[row][column] not in (None, "") or notes.get((row, column)) for row, column in positions-{(r0, c0)}):
            raise ValueError("legacy merge overlaps or hides data")
        occupied.update(positions)
        new_start = mapping[c0] - int(is_header)
        result.append([r0, r1, new_start, mapping[c1-1]+1])
    return result


def _hash(value) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()
