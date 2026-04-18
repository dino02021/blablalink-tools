# -*- coding: utf-8 -*-
"""Upload healthcheck member list to the CharacterDetails spreadsheet."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Dict, Iterable, List
from urllib import error, request

from gspread.utils import rowcol_to_a1

from ..constants import WEB_APP_URL
from ..data_store import build_healthcheck_upload_rows
from ..google_sheets.auth import get_gspread_client
from ..log import log_exc
from .common import JobResult
from .nikke_upload import resolve_character_sheet_for_account

_COLUMN_WIDTH_CACHE: dict[int, list[int]] = {}

HEALTHCHECK_WORKSHEET = "健檢名單"
SCORE_RESULT_WORKSHEET = "練度評分結果"
HEALTHCHECK_HEADERS = ["名稱", "屬性", "類型", "暱稱", "優先度"]
HEALTHCHECK_TYPE_ORDER = ("輸出", "輔助")
HEALTHCHECK_ELEMENT_ORDER = ("電擊", "水冷", "燃燒", "風壓", "鐵甲")
TRAINING_SHEET_PREFIX = "角色練度_"
TRAINING_CHANGE_WORKSHEET = "角色練度_變化"
STANDARD_WORKSHEET = "練度標準"
TOTAL_LABEL = "總分"
RANK_LABEL = "排名"
STANDARD_ROW2_VALUES = ["技能", "技能", "技能", "裝備(頭/手/身/腳)", "裝備(頭/手/身/腳)", "裝備(頭/手/身/腳)", "裝備(頭/手/身/腳)", "收藏品", "詞條(優/攻/彈)", "詞條(優/攻/彈)", "詞條(優/攻/彈)"]


def _post_healthcheck_nickname_sync(sheet_id: str) -> tuple[bool, str]:
    if not WEB_APP_URL:
        return False, "WEB_APP_URL 未設定"

    payload = {
        "kind": "sync_healthcheck_members",
        "sheet_id": sheet_id,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        WEB_APP_URL,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=600) as resp:
            body = resp.read().decode("utf-8", errors="replace").strip()
            ok = body.lower() == "ok"
            return ok, body or ("ok" if ok else "unknown_response")
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return False, body or f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return False, str(exc)


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_label(value: Any) -> str:
    return _normalize_text(value).replace(" ", "").replace("：", ":")


def _build_group_key(member_type: str, element: str) -> str:
    return f"{_normalize_text(member_type)}\u0001{_normalize_text(element)}"


def _pad_row(row: Iterable[Any], width: int) -> List[str]:
    values = [str(item or "") for item in row]
    if len(values) < width:
        values.extend([""] * (width - len(values)))
    return values[:width]


def _get_used_values(ws) -> list[list[str]]:
    return ws.get_all_values()


def _get_used_last_row(ws) -> int:
    return max(len(_get_used_values(ws)), 1)


def _get_row_values(ws, row: int) -> list[str]:
    return ws.row_values(row)


def _get_header_groups(values: list[str], start_col: int) -> list[dict[str, int]]:
    groups: list[dict[str, int]] = []
    if not values:
        return groups
    group_start = start_col
    for idx in range(1, len(values)):
        value = values[idx]
        if value != "" and value is not None:
            end = start_col + idx - 1
            groups.append({"start": group_start, "end": end, "width": end - group_start + 1})
            group_start = start_col + idx
    end = start_col + len(values) - 1
    groups.append({"start": group_start, "end": end, "width": end - group_start + 1})
    return groups


def _get_group_labels(values: list[str], start_col: int, groups: list[dict[str, int]]) -> list[str]:
    labels: list[str] = []
    for group in groups:
        idx = group["start"] - start_col
        labels.append(_normalize_text(values[idx]) if 0 <= idx < len(values) else "")
    return labels


def _get_merged_profile(ws, row: int, start_col: int) -> dict[str, Any]:
    values = _get_row_values(ws, row)
    if len(values) < start_col:
        return {"groups": [], "labels": [], "end_col": start_col - 1, "values": []}
    segment = values[start_col - 1 :]
    while segment and _normalize_text(segment[-1]) == "":
        segment.pop()
    if not segment:
        return {"groups": [], "labels": [], "end_col": start_col - 1, "values": []}
    groups = _get_header_groups(segment, start_col)
    labels = _get_group_labels(segment, start_col, groups)
    return {"groups": groups, "labels": labels, "end_col": start_col + len(segment) - 1, "values": segment}


def _find_insert_col(
    existing_names: list[str],
    existing_groups: list[dict[str, int]],
    desired_names: list[str],
    desired_name: str,
    fallback_col: int,
) -> int:
    idx = desired_names.index(desired_name)
    for next_name in desired_names[idx + 1 :]:
        if next_name in existing_names:
            return existing_groups[existing_names.index(next_name)]["start"]
    return fallback_col


def _ensure_min_columns(ws, width: int) -> None:
    if ws.col_count < width:
        ws.add_cols(width - ws.col_count)


def _batch_update(spreadsheet, requests: list[dict[str, Any]]) -> None:
    if not requests:
        return
    spreadsheet.batch_update({"requests": requests})


def _insert_columns_copy_template(
    ws,
    insert_col: int,
    width: int,
    template_start_col: int,
    used_last_row: int,
    copy_start_row: int = 1,
) -> None:
    source_start_col = template_start_col + width if insert_col <= template_start_col else template_start_col
    source_end_col = source_start_col + width - 1
    requests: list[dict[str, Any]] = [
        {
            "insertDimension": {
                "range": {
                    "sheetId": ws.id,
                    "dimension": "COLUMNS",
                    "startIndex": insert_col - 1,
                    "endIndex": insert_col - 1 + width,
                },
                "inheritFromBefore": False,
            }
        }
    ]
    if used_last_row >= copy_start_row:
        requests.append(
            {
            "copyPaste": {
                "source": {
                    "sheetId": ws.id,
                    "startRowIndex": copy_start_row - 1,
                    "endRowIndex": used_last_row,
                    "startColumnIndex": source_start_col - 1,
                    "endColumnIndex": source_end_col,
                },
                "destination": {
                    "sheetId": ws.id,
                    "startRowIndex": copy_start_row - 1,
                    "endRowIndex": used_last_row,
                    "startColumnIndex": insert_col - 1,
                    "endColumnIndex": insert_col - 1 + width,
                },
                "pasteType": "PASTE_NORMAL",
                "pasteOrientation": "NORMAL",
            },
        }
        )
    _batch_update(ws.spreadsheet, requests)


def _clear_inserted_content(ws, start_row: int, start_col: int, end_row: int, width: int) -> None:
    if end_row < start_row or width <= 0:
        return
    start = rowcol_to_a1(start_row, start_col)
    end = rowcol_to_a1(end_row, start_col + width - 1)
    ws.batch_clear([f"{start}:{end}"])


def _invalidate_column_width_cache(ws) -> None:
    _COLUMN_WIDTH_CACHE.pop(ws.id, None)


def _get_sheet_column_widths(ws) -> list[int]:
    cached = _COLUMN_WIDTH_CACHE.get(ws.id)
    if cached is not None:
        return cached
    meta = ws.spreadsheet.fetch_sheet_metadata(
        params={
            "ranges": [ws.title],
            "includeGridData": True,
        }
    )
    for sheet in meta.get("sheets", []):
        props = sheet.get("properties", {})
        if props.get("sheetId") != ws.id:
            continue
        data = sheet.get("data", [])
        if not data:
            break
        column_meta = data[0].get("columnMetadata", [])
        widths = [int(col.get("pixelSize") or 100) for col in column_meta]
        _COLUMN_WIDTH_CACHE[ws.id] = widths
        return widths
    _COLUMN_WIDTH_CACHE[ws.id] = []
    return []


def _get_column_widths(ws, start_col: int, width: int) -> list[int]:
    if width <= 0:
        return []
    all_widths = _get_sheet_column_widths(ws)
    if not all_widths:
        return [100] * width
    start_idx = max(start_col - 1, 0)
    end_idx = start_idx + width
    sliced = all_widths[start_idx:end_idx]
    if len(sliced) < width:
        sliced.extend([100] * (width - len(sliced)))
    return sliced


def _set_column_widths(ws, start_col: int, widths: list[int]) -> None:
    if not widths:
        return
    requests: list[dict[str, Any]] = []
    run_start = start_col
    run_width = widths[0]
    run_len = 1
    for offset in range(1, len(widths)):
        current = widths[offset]
        if current == run_width:
            run_len += 1
            continue
        requests.append(
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": ws.id,
                        "dimension": "COLUMNS",
                        "startIndex": run_start - 1,
                        "endIndex": run_start - 1 + run_len,
                    },
                    "properties": {"pixelSize": run_width},
                    "fields": "pixelSize",
                }
            }
        )
        run_start = start_col + offset
        run_width = current
        run_len = 1
    requests.append(
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": ws.id,
                    "dimension": "COLUMNS",
                    "startIndex": run_start - 1,
                    "endIndex": run_start - 1 + run_len,
                },
                "properties": {"pixelSize": run_width},
                "fields": "pixelSize",
            }
        }
    )
    _batch_update(ws.spreadsheet, requests)


def _delete_columns(ws, start_col: int, end_col: int) -> None:
    if end_col < start_col:
        return
    _invalidate_column_width_cache(ws)
    _batch_update(
        ws.spreadsheet,
        [
            {
                "deleteDimension": {
                    "range": {
                        "sheetId": ws.id,
                        "dimension": "COLUMNS",
                        "startIndex": start_col - 1,
                        "endIndex": end_col,
                    }
                }
            }
        ],
    )


def _append_template_block_and_move(
    ws,
    insert_col: int,
    width: int,
    template_start_col: int,
    used_last_row: int,
) -> int:
    append_start_col = ws.col_count + 1
    _invalidate_column_width_cache(ws)
    ws.add_cols(width)
    _batch_update(
        ws.spreadsheet,
        [
            {
                "copyPaste": {
                    "source": {
                        "sheetId": ws.id,
                        "startRowIndex": 0,
                        "endRowIndex": used_last_row,
                        "startColumnIndex": template_start_col - 1,
                        "endColumnIndex": template_start_col - 1 + width,
                    },
                    "destination": {
                        "sheetId": ws.id,
                        "startRowIndex": 0,
                        "endRowIndex": used_last_row,
                        "startColumnIndex": append_start_col - 1,
                        "endColumnIndex": append_start_col - 1 + width,
                    },
                    "pasteType": "PASTE_NORMAL",
                    "pasteOrientation": "NORMAL",
                }
            }
        ],
    )
    if insert_col != append_start_col:
        _batch_update(
            ws.spreadsheet,
            [
                {
                    "moveDimension": {
                        "source": {
                            "sheetId": ws.id,
                            "dimension": "COLUMNS",
                            "startIndex": append_start_col - 1,
                            "endIndex": append_start_col - 1 + width,
                        },
                        "destinationIndex": insert_col - 1,
                    }
                }
            ],
        )
        return insert_col
    return append_start_col


def _trim_training_element_trailing_columns(ws, expected_width: int) -> None:
    while True:
        profile = _get_merged_profile(ws, 1, 2)
        groups = profile["groups"]
        labels = profile["labels"]
        if not groups:
            return
        last_group = groups[-1]
        last_label = _normalize_text(labels[-1]) if labels else ""
        if last_group["width"] == expected_width or last_label:
            return
        _delete_columns(ws, int(last_group["start"]), int(last_group["end"]))


def _repair_training_element_widths(ws) -> None:
    profile = _get_merged_profile(ws, 1, 2)
    template_group = next((group for group in profile["groups"] if int(group["width"]) == 11), None)
    if not template_group:
        return
    template_widths = _get_column_widths(ws, int(template_group["start"]), 11)
    for group in profile["groups"]:
        if int(group["width"]) != 11:
            continue
        _set_column_widths(ws, int(group["start"]), template_widths)


def _trim_training_change_trailing_columns(ws, expected_width: int) -> None:
    while True:
        profile = _get_merged_profile(ws, 2, 2)
        groups = profile["groups"]
        labels = profile["labels"]
        if not groups:
            return
        last_group = groups[-1]
        last_label = _normalize_text(labels[-1]) if labels else ""
        if last_group["width"] == expected_width or last_label:
            return
        _delete_columns(ws, int(last_group["start"]), int(last_group["end"]))


def _repair_training_change_widths(ws) -> None:
    profile = _get_merged_profile(ws, 2, 2)
    template_group = next((group for group in profile["groups"] if int(group["width"]) == 11), None)
    if not template_group:
        return
    template_widths = _get_column_widths(ws, int(template_group["start"]), 11)
    for group in profile["groups"]:
        if int(group["width"]) != 11:
            continue
        _set_column_widths(ws, int(group["start"]), template_widths)


def _sync_healthcheck_sheet(spreadsheet, rows: list[dict[str, str]]) -> list[dict[str, str]]:
    ws = spreadsheet.worksheet(HEALTHCHECK_WORKSHEET)
    existing_values = _get_used_values(ws)
    width = max(max((len(row) for row in existing_values), default=0), len(HEALTHCHECK_HEADERS))
    _ensure_min_columns(ws, width)

    existing_rows = [_pad_row(row, width) for row in existing_values[1:]] if existing_values else []
    existing_by_name: dict[str, list[str]] = {}
    for row in existing_rows:
        name = _normalize_text(row[0])
        if name and name not in existing_by_name:
            existing_by_name[name] = row[:]

    output_rows: list[list[str]] = []
    synced_entries: list[dict[str, str]] = []
    for row in rows:
        name = _normalize_text(row.get("name"))
        element = _normalize_text(row.get("element"))
        member_type = _normalize_text(row.get("type"))
        full_row = existing_by_name.get(name, [""] * width)[:]
        while len(full_row) < width:
            full_row.append("")
        full_row[0] = name
        full_row[1] = element
        full_row[2] = member_type
        output_rows.append(full_row)
        synced_entries.append(
            {
                "name": name,
                "element": element,
                "type": member_type,
                "nickname": _normalize_text(full_row[3]) or name,
                "priority": _normalize_text(full_row[4]),
            }
        )

    header_range = f"A1:{rowcol_to_a1(1, len(HEALTHCHECK_HEADERS))}"
    ws.update(header_range, [HEALTHCHECK_HEADERS])
    if output_rows:
        ws.update(f"A2:{rowcol_to_a1(len(output_rows) + 1, width)}", output_rows)

    old_count = len(existing_rows)
    if old_count > len(output_rows):
        ws.delete_rows(len(output_rows) + 2, old_count - len(output_rows))
    elif not output_rows and ws.row_count > 1 and len(existing_values) > 1:
        ws.delete_rows(2, len(existing_values) - 1)

    return synced_entries


def _build_desired_names_by_element(entries: list[dict[str, str]]) -> dict[str, list[str]]:
    desired_by_element: dict[str, list[str]] = defaultdict(list)
    seen: dict[str, set[str]] = defaultdict(set)
    for row in entries:
        element = _normalize_text(row.get("element"))
        name = _normalize_text(row.get("name"))
        if not element or not name or name in seen[element]:
            continue
        seen[element].add(name)
        desired_by_element[element].append(name)
    return desired_by_element


def _build_desired_standard_names(entries: list[dict[str, str]]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for row in entries:
        name = _normalize_text(row.get("name"))
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def _sync_training_element_sheet(spreadsheet, element: str, desired_names: list[str]) -> int:
    if not desired_names:
        return 0
    ws = spreadsheet.worksheet(f"{TRAINING_SHEET_PREFIX}{element}")
    inserted = 0

    for _ in range(len(desired_names)):
        _trim_training_element_trailing_columns(ws, 11)
        profile = _get_merged_profile(ws, 1, 2)
        existing_names = [_normalize_text(label) for label in profile["labels"]]
        existing_groups = profile["groups"]
        if not existing_groups:
            return inserted
        template_group = existing_groups[0]
        width = int(template_group["width"])
        template_widths = _get_column_widths(ws, int(template_group["start"]), width)
        template_row2 = _pad_row(_get_row_values(ws, 2), profile["end_col"])[template_group["start"] - 1 : template_group["end"]]
        changed = False

        for name in desired_names:
            if name in existing_names:
                continue
            fallback_col = existing_groups[-1]["end"] + 1
            insert_col = _find_insert_col(existing_names, existing_groups, desired_names, name, fallback_col)
            used_last_row = max(_get_used_last_row(ws), 3)
            if insert_col >= fallback_col:
                append_start_col = ws.col_count + 1
                _invalidate_column_width_cache(ws)
                ws.add_cols(width)
                _batch_update(
                    ws.spreadsheet,
                    [
                        {
                            "copyPaste": {
                                "source": {
                                    "sheetId": ws.id,
                                    "startRowIndex": 0,
                                    "endRowIndex": used_last_row,
                                    "startColumnIndex": template_group["start"] - 1,
                                    "endColumnIndex": template_group["start"] - 1 + width,
                                },
                                "destination": {
                                    "sheetId": ws.id,
                                    "startRowIndex": 0,
                                    "endRowIndex": used_last_row,
                                    "startColumnIndex": append_start_col - 1,
                                    "endColumnIndex": append_start_col - 1 + width,
                                },
                                "pasteType": "PASTE_NORMAL",
                                "pasteOrientation": "NORMAL",
                            }
                        }
                    ],
                )
                insert_col = append_start_col
            else:
                insert_col = _append_template_block_and_move(
                    ws,
                    insert_col,
                    width,
                    template_group["start"],
                    used_last_row,
                )
            _set_column_widths(ws, insert_col, template_widths)
            ws.update(f"{rowcol_to_a1(1, insert_col)}:{rowcol_to_a1(2, insert_col + width - 1)}", [[name] + [""] * (width - 1), template_row2])
            _clear_inserted_content(ws, 3, insert_col, used_last_row, width)
            inserted += 1
            changed = True
            break
        if not changed:
            break
    _repair_training_element_widths(ws)
    return inserted


def _group_label_by_col(row_values: list[str], start_col: int, target_col: int) -> str:
    current = ""
    for col in range(start_col, target_col + 1):
        idx = col - start_col
        if 0 <= idx < len(row_values):
            value = row_values[idx]
            if value not in ("", None):
                current = _normalize_text(value)
    return current


def _sync_training_change_sheet(spreadsheet, desired_by_element: dict[str, list[str]]) -> int:
    ws = spreadsheet.worksheet(TRAINING_CHANGE_WORKSHEET)
    inserted = 0
    for _ in range(sum(len(items) for items in desired_by_element.values()) or 1):
        _trim_training_change_trailing_columns(ws, 11)
        row2 = _get_merged_profile(ws, 2, 2)
        if not row2["groups"]:
            return inserted
        row1_values = _get_row_values(ws, 1)[1 : row2["end_col"]]
        changed = False

        for element in HEALTHCHECK_ELEMENT_ORDER:
            desired_names = desired_by_element.get(element) or []
            if not desired_names:
                continue
            element_groups: list[dict[str, int]] = []
            element_names: list[str] = []
            for idx, group in enumerate(row2["groups"]):
                owner = _group_label_by_col(row1_values, 2, group["start"])
                if owner != element:
                    continue
                element_groups.append(group)
                element_names.append(_normalize_text(row2["labels"][idx]))
            if not element_groups:
                continue
            template_group = element_groups[0]
            width = int(template_group["width"])
            template_widths = _get_column_widths(ws, int(template_group["start"]), width)
            template_row3 = _pad_row(_get_row_values(ws, 3), row2["end_col"])[template_group["start"] - 1 : template_group["end"]]

            for name in desired_names:
                if name in element_names:
                    continue
                fallback_col = element_groups[-1]["end"] + 1
                insert_col = _find_insert_col(element_names, element_groups, desired_names, name, fallback_col)
                used_last_row = max(_get_used_last_row(ws), 4)
                if insert_col >= fallback_col and element_groups[-1]["end"] == row2["end_col"]:
                    append_start_col = ws.col_count + 1
                    _invalidate_column_width_cache(ws)
                    ws.add_cols(width)
                    _batch_update(
                        ws.spreadsheet,
                        [
                            {
                                "copyPaste": {
                                    "source": {
                                        "sheetId": ws.id,
                                        "startRowIndex": 2,
                                        "endRowIndex": used_last_row,
                                        "startColumnIndex": template_group["start"] - 1,
                                        "endColumnIndex": template_group["start"] - 1 + width,
                                    },
                                    "destination": {
                                        "sheetId": ws.id,
                                        "startRowIndex": 2,
                                        "endRowIndex": used_last_row,
                                        "startColumnIndex": append_start_col - 1,
                                        "endColumnIndex": append_start_col - 1 + width,
                                    },
                                    "pasteType": "PASTE_NORMAL",
                                    "pasteOrientation": "NORMAL",
                                }
                            },
                            {
                                "mergeCells": {
                                    "range": {
                                        "sheetId": ws.id,
                                        "startRowIndex": 1,
                                        "endRowIndex": 2,
                                        "startColumnIndex": append_start_col - 1,
                                        "endColumnIndex": append_start_col - 1 + width,
                                    },
                                    "mergeType": "MERGE_ALL",
                                }
                            },
                        ],
                    )
                    insert_col = append_start_col
                else:
                    _invalidate_column_width_cache(ws)
                    _insert_columns_copy_template(
                        ws,
                        insert_col,
                        width,
                        template_group["start"],
                        used_last_row,
                        copy_start_row=3,
                    )
                    _batch_update(
                        spreadsheet,
                        [
                            {
                                "mergeCells": {
                                    "range": {
                                        "sheetId": ws.id,
                                        "startRowIndex": 1,
                                        "endRowIndex": 2,
                                        "startColumnIndex": insert_col - 1,
                                        "endColumnIndex": insert_col - 1 + width,
                                    },
                                    "mergeType": "MERGE_ALL",
                                }
                            },
                        ],
                    )
                _set_column_widths(ws, insert_col, template_widths)
                ws.update(
                    f"{rowcol_to_a1(2, insert_col)}:{rowcol_to_a1(3, insert_col + width - 1)}",
                    [[name] + [""] * (width - 1), template_row3],
                )
                _clear_inserted_content(ws, 4, insert_col, used_last_row, width)
                inserted += 1
                changed = True
                break
            if changed:
                break
        if not changed:
            break
    _repair_training_change_widths(ws)
    return inserted


def _set_standard_template(ws, start_col: int, name: str) -> None:
    requests = [
        {
            "mergeCells": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": start_col - 1,
                    "endColumnIndex": start_col + 10,
                },
                "mergeType": "MERGE_ALL",
            }
        },
        {
            "mergeCells": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 1,
                    "endRowIndex": 2,
                    "startColumnIndex": start_col - 1,
                    "endColumnIndex": start_col + 2,
                },
                "mergeType": "MERGE_ALL",
            }
        },
        {
            "mergeCells": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 1,
                    "endRowIndex": 2,
                    "startColumnIndex": start_col + 2,
                    "endColumnIndex": start_col + 6,
                },
                "mergeType": "MERGE_ALL",
            }
        },
        {
            "mergeCells": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": 1,
                    "endRowIndex": 2,
                    "startColumnIndex": start_col + 7,
                    "endColumnIndex": start_col + 10,
                },
                "mergeType": "MERGE_ALL",
            }
        },
    ]
    _batch_update(ws.spreadsheet, requests)
    ws.update(
        f"{rowcol_to_a1(1, start_col)}:{rowcol_to_a1(2, start_col + 10)}",
        [
            [name] + [""] * 10,
            STANDARD_ROW2_VALUES,
        ],
    )
    _set_standard_column_widths(ws, start_col)


def _set_standard_column_widths(ws, start_col: int) -> None:
    requests = [
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": ws.id,
                    "dimension": "COLUMNS",
                    "startIndex": start_col - 1,
                    "endIndex": start_col + 7,
                },
                "properties": {"pixelSize": 30},
                "fields": "pixelSize",
            }
        },
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": ws.id,
                    "dimension": "COLUMNS",
                    "startIndex": start_col + 6,
                    "endIndex": start_col + 7,
                },
                "properties": {"pixelSize": 47},
                "fields": "pixelSize",
            }
        },
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": ws.id,
                    "dimension": "COLUMNS",
                    "startIndex": start_col + 7,
                    "endIndex": start_col + 10,
                },
                "properties": {"pixelSize": 50},
                "fields": "pixelSize",
            }
        },
    ]
    _batch_update(ws.spreadsheet, requests)


def _repair_standard_column_widths(ws) -> None:
    profile = _get_merged_profile(ws, 1, 2)
    for group in profile["groups"]:
        if int(group["width"]) != 11:
            continue
        _set_standard_column_widths(ws, int(group["start"]))


def _sync_standard_sheet(spreadsheet, desired_names: list[str]) -> int:
    if not desired_names:
        return 0
    ws = spreadsheet.worksheet(STANDARD_WORKSHEET)
    inserted = 0
    for _ in range(len(desired_names)):
        profile = _get_merged_profile(ws, 1, 2)
        existing_names = [_normalize_text(label) for label in profile["labels"]]
        existing_groups = profile["groups"]
        changed = False

        for name in desired_names:
            if name in existing_names:
                continue
            fallback_col = existing_groups[-1]["end"] + 1 if existing_groups else 2
            insert_col = _find_insert_col(existing_names, existing_groups, desired_names, name, fallback_col) if existing_groups else fallback_col
            used_last_row = max(_get_used_last_row(ws), 3)
            if insert_col >= fallback_col and (not existing_groups or existing_groups[-1]["end"] == profile["end_col"]):
                _invalidate_column_width_cache(ws)
                ws.add_cols(11)
                insert_col = ws.col_count - 10
            else:
                _invalidate_column_width_cache(ws)
                _batch_update(
                    spreadsheet,
                    [
                        {
                            "insertDimension": {
                                "range": {
                                    "sheetId": ws.id,
                                    "dimension": "COLUMNS",
                                    "startIndex": insert_col - 1,
                                    "endIndex": insert_col + 10,
                                },
                                "inheritFromBefore": False,
                            }
                        }
                    ],
                )
            _set_standard_template(ws, insert_col, name)
            _clear_inserted_content(ws, 3, insert_col, used_last_row, 11)
            inserted += 1
            changed = True
            break
        if not changed:
            break
    _repair_standard_column_widths(ws)
    return inserted


def _build_score_result_blocks(ws) -> dict[str, dict[str, Any]]:
    values = _get_used_values(ws)
    if len(values) < 3:
        return {}
    row1 = values[0]
    row2 = values[1]
    row3 = values[2]
    width = max(len(row1), len(row2), len(row3))
    row1 = _pad_row(row1, width)
    row2 = _pad_row(row2, width)
    row3 = _pad_row(row3, width)

    blocks: dict[str, dict[str, Any]] = {}
    current_type = ""
    current_element = ""
    for col in range(4, width + 1):
        idx = col - 1
        type_cell = _normalize_text(row1[idx])
        element_cell = _normalize_text(row2[idx])
        if type_cell:
            current_type = type_cell
        if element_cell:
            current_element = element_cell
        if not current_type or not current_element:
            continue

        key = _build_group_key(current_type, current_element)
        block = blocks.setdefault(
            key,
            {
                "type": current_type,
                "element": current_element,
                "char_cols": [],
                "total_col": None,
                "rank_col": None,
            },
        )
        label = _normalize_text(row3[idx])
        label_norm = _normalize_label(label)
        if label_norm == _normalize_label(TOTAL_LABEL):
            block["total_col"] = col
            continue
        if label_norm == _normalize_label(RANK_LABEL):
            block["rank_col"] = col
            continue
        block["char_cols"].append({"col": col, "label": label})
    return blocks


def _insert_score_result_column(ws, insert_col: int, template_col: int, nickname: str) -> None:
    used_last_row = max(_get_used_last_row(ws), 3)
    _insert_columns_copy_template(
        ws,
        insert_col,
        1,
        template_col,
        used_last_row,
        copy_start_row=3,
    )
    ws.update_cell(3, insert_col, nickname)
    _clear_inserted_content(ws, 4, insert_col, used_last_row, 1)


def _sync_score_result_columns(spreadsheet, entries: list[dict[str, str]]) -> int:
    ws = spreadsheet.worksheet(SCORE_RESULT_WORKSHEET)
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for entry in entries:
        key = _build_group_key(entry.get("type", ""), entry.get("element", ""))
        grouped[key].append(entry)

    inserted = 0
    for member_type in HEALTHCHECK_TYPE_ORDER:
        for element in HEALTHCHECK_ELEMENT_ORDER:
            key = _build_group_key(member_type, element)
            desired_entries = grouped.get(key) or []
            if not desired_entries:
                continue
            while True:
                blocks = _build_score_result_blocks(ws)
                block = blocks.get(key)
                if not block:
                    raise ValueError(f"練度評分結果找不到區塊: type={member_type} element={element}")
                current_labels = [_normalize_label(item["label"]) for item in block["char_cols"]]
                desired_nicknames = [_normalize_text(item.get("nickname") or item.get("name")) for item in desired_entries]
                if len(block["char_cols"]) >= len(desired_entries):
                    break
                missing = next((nickname for nickname in desired_nicknames if _normalize_label(nickname) not in current_labels), None)
                if not missing:
                    break
                desired_idx = desired_nicknames.index(missing)
                insert_col = int(block["total_col"] or block["rank_col"] or (block["char_cols"][-1]["col"] + 1 if block["char_cols"] else 4))
                for next_nickname in desired_nicknames[desired_idx + 1 :]:
                    next_norm = _normalize_label(next_nickname)
                    for item in block["char_cols"]:
                        if _normalize_label(item["label"]) == next_norm:
                            insert_col = int(item["col"])
                            break
                    else:
                        continue
                    break
                template_col = int(block["char_cols"][0]["col"]) if block["char_cols"] else max(1, insert_col - 1)
                _insert_score_result_column(ws, insert_col, template_col, missing)
                inserted += 1
    return inserted


def _sync_all_structural_sheets(spreadsheet, entries: list[dict[str, str]]) -> dict[str, int]:
    desired_by_element = _build_desired_names_by_element(entries)
    desired_standard_names = _build_desired_standard_names(entries)

    stats = {
        "training_element_columns": 0,
        "training_change_columns": 0,
        "standard_columns": 0,
        "score_result_columns": 0,
    }
    for element, desired_names in desired_by_element.items():
        stats["training_element_columns"] += _sync_training_element_sheet(spreadsheet, element, desired_names)
    stats["training_change_columns"] = _sync_training_change_sheet(spreadsheet, desired_by_element)
    stats["standard_columns"] = _sync_standard_sheet(spreadsheet, desired_standard_names)
    stats["score_result_columns"] = _sync_score_result_columns(spreadsheet, entries)
    return stats


def run_healthcheck_upload(account: str | None = None) -> JobResult:
    rows = build_healthcheck_upload_rows()
    if not rows:
        return JobResult(ok=False, message="nikke_healthcheck_members.json 沒有可上傳的資料")

    try:
        union_id, target_sheet_id, target_sheet_name = resolve_character_sheet_for_account(account)
    except ValueError as exc:
        return JobResult(ok=False, message=str(exc))

    outputs = {
        "union_id": union_id,
        "sheet_id": target_sheet_id,
        "sheet_name": target_sheet_name,
        "row_count": len(rows),
    }

    try:
        cli = get_gspread_client()
        spreadsheet = cli.open_by_key(target_sheet_id)
        synced_entries = _sync_healthcheck_sheet(spreadsheet, rows)
        structure_stats = _sync_all_structural_sheets(spreadsheet, synced_entries)
        outputs.update(structure_stats)
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return JobResult(ok=False, message=f"健檢名單同步失敗: {exc}", outputs=outputs)

    ok, message = _post_healthcheck_nickname_sync(target_sheet_id)
    if not ok:
        return JobResult(ok=False, message=message or "健檢名單已同步，但 gs 暱稱同步失敗", outputs=outputs)

    return JobResult(
        ok=True,
        message=f"健檢名單已同步 ({len(rows)})",
        outputs=outputs,
    )
