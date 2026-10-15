"""Decode local/mock legacy checklist plus six-row, twelve-column standard blocks."""

from dataclasses import dataclass
import re
import unicodedata

from adapters.sheet_standard import StandardReference, read_standard_sheet
from domain.healthcheck import ELEMENTS, parse_score_number
from domain.snapshots import canonical_json
from domain.standard_layout import ABBREVIATIONS, METRICS

HEADERS = ("名稱", "屬性", "類型", "詞條1", "詞條2", "詞條3", "評分追蹤", "暱稱", "優先度")


@dataclass(frozen=True)
class LegacyCatalog:
    reference: StandardReference
    role_aliases: tuple[tuple[str, str], ...]
    function_aliases: tuple[tuple[str, str], ...] = ()
    dual_elements: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def __post_init__(self):
        for field, allowed in (("role_aliases", self.reference.role_ids), ("function_aliases", self.reference.function_ids)):
            aliases = tuple(tuple(pair) for pair in getattr(self, field))
            if any(len(pair) != 2 or not isinstance(pair[0], str) or not _key(pair[0]) or pair[1] not in allowed for pair in aliases):
                raise ValueError("invalid pinned alias dictionary")
            object.__setattr__(self, field, aliases)
        dual = tuple((code, tuple(elements)) for code, elements in self.dual_elements)
        if len(dict(dual)) != len(dual) or any(code not in self.reference.role_ids or len(elements) != 2
                or len(set(elements)) != 2 or any(e not in ELEMENTS for e in elements) for code, elements in dual):
            raise ValueError("invalid pinned dual-element exception")
        object.__setattr__(self, "dual_elements", dual)


def read_legacy_standard(book: dict, scope_id: str, captured_at: str, catalog: LegacyCatalog):
    inputs = book["inputs"]
    checklist, grid = inputs["legacy_checklist"], inputs["legacy_standard"]
    entries = _checklist(checklist, catalog)
    if not isinstance(grid, list) or len(grid) < 6 or any(not isinstance(row, list) for row in grid[:6]):
        raise ValueError("six explicit standard rows required")
    starts = [i for i, value in enumerate(grid[1]) if i >= 1 and _key(value)]
    if not starts or starts != [1 + 12 * i for i in range(len(starts))]:
        raise ValueError("standard requires contiguous twelve-column blocks from B; preview migration first")
    end = starts[-1] + 12
    column_count = inputs.get("standard_column_count", max(len(row) for row in grid[:6]))
    if type(column_count) is not int or column_count < end:
        raise ValueError("standard physical columns are shorter than the template")
    if any(any(_key(v) for v in row[end:]) for row in grid[:6]):
        raise ValueError("unregistered standard columns contain data")
    by_role, seen_owner = {}, set()
    owner = ""
    for start in starts:
        if _key(_cell(grid, 0, start)):
            owner = str(_cell(grid, 0, start)).strip()
        if owner not in ELEMENTS:
            raise ValueError("standard block requires a known element owner")
        label = _key(_cell(grid, 1, start))
        explicit = inputs.get("standard_role_ids", {}).get(str(start + 1))
        if explicit is not None:
            explicit = str(explicit)
        matches = {row["name_code"] for row in entries if label in row["aliases"]}
        if explicit is not None:
            if matches and explicit not in matches:
                raise ValueError("protected standard ID contradicts a known role label")
            matches = {explicit} if explicit in {row["name_code"] for row in entries} else set()
        if len(matches) != 1:
            raise ValueError("standard role header cannot resolve to one stable ID")
        code = next(iter(matches))
        entry = next(row for row in entries if row["name_code"] == code)
        if owner not in entry["elements"] or (code, owner) in seen_owner:
            raise ValueError("standard element ownership conflicts or repeats")
        seen_owner.add((code, owner))
        for offset, expected in ((0, "突破"), (1, "技能"), (4, "裝備 (頭/手/身/腳)"), (8, "收藏品")):
            if _key(_cell(grid, 2, start + offset)) != _key(expected):
                raise ValueError("standard group offset differs from twelve-column template")
        traits = entry["traits"]
        trait_names = [name for name, fid in traits if fid]
        expected_trait = "詞條" + (" (" + "/".join(ABBREVIATIONS.get(name, name) for name in trait_names) + ")" if trait_names else "")
        if _key(_cell(grid, 2, start + 9)) != _key(expected_trait):
            raise ValueError("standard trait header differs from selected function identities")
        metrics = [{"metric_id": metric, "threshold": _cell(grid, 3, start + offset),
                    "coefficient": _cell(grid, 4, start + offset)} for offset, metric in enumerate(METRICS)]
        for slot, (_, function_id) in enumerate(traits):
            threshold, coefficient = _cell(grid, 3, start + 9 + slot), _cell(grid, 4, start + 9 + slot)
            if function_id:
                metrics.append({"metric_id": f"trait_{slot + 1}", "function_id": function_id,
                                "threshold": threshold, "coefficient": coefficient})
            elif _key(threshold) or _key(coefficient):
                raise ValueError("standard value refers to an unselected trait slot")
        weights = [parse_score_number(_cell(grid, 5, start + offset)) for offset in range(12) if _key(_cell(grid, 5, start + offset))]
        if any(w is None for w in weights) or len(set(weights)) > 1:
            raise ValueError("standard merged total weight is invalid or inconsistent")
        standard = {"metrics": metrics, "total_weight": weights[0] if weights else 0}
        if code in by_role and canonical_json(by_role[code]) != canonical_json(standard):
            raise ValueError("dual-element copies disagree on one shared role standard")
        by_role[code] = standard
    if set(by_role) != {row["name_code"] for row in entries}:
        raise ValueError("standard blocks do not cover the checklist")
    structured = []
    for entry in entries:
        structured.append({key: value for key, value in entry.items() if key not in ("aliases", "traits")})
        structured[-1].update(by_role[entry["name_code"]])
    return read_standard_sheet({"inputs": {"standard_rows": structured}}, scope_id, captured_at, catalog.reference)


def _checklist(rows, catalog):
    if not isinstance(rows, list) or not rows or any(not isinstance(row, list) for row in rows):
        raise ValueError("explicit checklist grid required")
    header = tuple(_key(v) for v in rows[0])
    legacy = header[:8] == tuple(_key(v) for v in (*HEADERS[:6], *HEADERS[7:]))
    expected = (*HEADERS[:6], *HEADERS[7:]) if legacy else HEADERS
    if header[:len(expected)] != tuple(_key(v) for v in expected):
        raise ValueError("unknown checklist headers")
    id_index = header.index(_key("角色ID")) if _key("角色ID") in header else None
    result = []
    for original in rows[1:]:
        row = list(original[:8 if legacy else 9])
        row.extend([""] * ((8 if legacy else 9) - len(row)))
        if not any(_key(value) for value in row):
            continue
        if legacy:
            row.insert(6, "")
        explicit = original[id_index] if id_index is not None and id_index < len(original) else None
        code = str(explicit).strip() if explicit is not None and str(explicit).strip() else _resolve(row[0], catalog.role_aliases)
        if code not in catalog.reference.role_ids:
            raise ValueError("unknown protected role ID")
        known_names = {target for alias, target in catalog.role_aliases if _key(alias) == _key(row[0])}
        if known_names and code not in known_names:
            raise ValueError("protected role ID contradicts a known role label")
        if not _key(row[0]) or row[1] not in ELEMENTS or row[2] not in ("輸出", "輔助"):
            raise ValueError("incomplete or invalid checklist row")
        elements = dict(catalog.dual_elements).get(code, (row[1],))
        if row[1] not in elements:
            raise ValueError("checklist element conflicts with pinned exception")
        traits = tuple((str(value).strip(), _resolve(value, catalog.function_aliases)) if _key(value) else ("", None) for value in row[3:6])
        identities = [fid for _, fid in traits if fid]
        if len(set(identities)) != len(identities):
            raise ValueError("duplicate selected function identity")
        existing = [item for item in result if item["name_code"] == code]
        if existing and existing[0]["traits"] != traits:
            raise ValueError("same role types select different trait identities")
        aliases = {_key(alias) for alias, target in catalog.role_aliases if target == code}
        aliases.update((_key(row[0]), _key(row[7])))
        result.append({"name_code": code, "display_name": str(row[7] or row[0]).strip(), "elements": list(elements),
            "role_type": "output" if row[2] == "輸出" else "support", "tracking": row[6] or "", "priority": row[8] or "",
            "trait_labels": {fid: name for name, fid in traits if fid}, "traits": traits, "aliases": aliases})
    if not result:
        raise ValueError("empty checklist cannot publish")
    return result


def _resolve(label, aliases):
    matches = {code for alias, code in aliases if _key(alias) == _key(label)}
    if len(matches) != 1:
        raise ValueError("dictionary label is unknown or ambiguous")
    return next(iter(matches))


def _key(value):
    return re.sub(r"[\s\u00a0\u200b-\u200d\ufeff]+", "", unicodedata.normalize("NFKC", str("" if value is None else value))).replace("：", ":")


def _cell(grid, row, column):
    return grid[row][column] if column < len(grid[row]) and grid[row][column] is not None else ""
