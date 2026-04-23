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
TEMPLATE_BLOCK_WIDTH = 11
TEMPLATE_COLUMN_WIDTHS = [30, 30, 30, 30, 30, 30, 30, 47, 50, 50, 50]
TEMPLATE_GROUPS = (
    (0, 3, "技能"),
    (3, 4, "裝備(頭/手/身/腳)"),
    (7, 1, "收藏品"),
    (8, 3, "詞條(優/攻/彈)"),
)
HEADER_BG = {"red": 247 / 255, "green": 203 / 255, "blue": 77 / 255}
DATA_BG_ODD = {"red": 1.0, "green": 1.0, "blue": 1.0}
DATA_BG_EVEN = {"red": 250 / 255, "green": 243 / 255, "blue": 224 / 255}
BORDER_STYLE = {"style": "SOLID"}
BORDER_COLOR = {"red": 102 / 255, "green": 0.0, "blue": 0.0}
BORDER_INNER_STYLE = {"style": "SOLID", "color": BORDER_COLOR}
BORDER_OUTER_STYLE = {"style": "SOLID_MEDIUM", "color": BORDER_COLOR}


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


def _last_nonempty_col(values: list[str]) -> int:
    for idx in range(len(values) - 1, -1, -1):
        if _normalize_text(values[idx]):
            return idx + 1
    return 0


def _get_fixed_width_profile(
    ws,
    label_row: int,
    start_col: int,
    width: int,
    extent_rows: tuple[int, ...],
) -> dict[str, Any]:
    row_values = {row: _get_row_values(ws, row) for row in extent_rows}
    end_col = max((_last_nonempty_col(values) for values in row_values.values()), default=0)
    if end_col < start_col:
        return {"groups": [], "labels": [], "end_col": start_col - 1, "values": []}
    block_count = ((end_col - start_col + 1) + width - 1) // width
    groups: list[dict[str, int]] = []
    labels: list[str] = []
    label_values = row_values.get(label_row, [])
    for idx in range(block_count):
        block_start = start_col + idx * width
        block_end = block_start + width - 1
        groups.append({"start": block_start, "end": block_end, "width": width})
        label_idx = block_start - 1
        label = label_values[label_idx] if 0 <= label_idx < len(label_values) else ""
        labels.append(_normalize_text(label))
    return {
        "groups": groups,
        "labels": labels,
        "end_col": start_col + block_count * width - 1,
        "values": [],
    }


def _find_insert_col_after_previous(
    existing_names: list[str],
    existing_groups: list[dict[str, int]],
    desired_names: list[str],
    desired_name: str,
    group_start_col: int,
) -> int:
    idx = desired_names.index(desired_name)
    for prev_name in reversed(desired_names[:idx]):
        if prev_name not in existing_names:
            continue
        prev_group = existing_groups[existing_names.index(prev_name)]
        return int(prev_group["end"]) + 1
    return group_start_col


def _ensure_min_columns(ws, width: int) -> None:
    if ws.col_count < width:
        ws.add_cols(width - ws.col_count)


def _batch_update(spreadsheet, requests: list[dict[str, Any]]) -> None:
    if not requests:
        return
    spreadsheet.batch_update({"requests": requests})


def _sheet_range(
    ws,
    start_row: int,
    end_row: int,
    start_col: int,
    end_col: int,
) -> dict[str, int]:
    return {
        "sheetId": ws.id,
        "startRowIndex": start_row - 1,
        "endRowIndex": end_row,
        "startColumnIndex": start_col - 1,
        "endColumnIndex": end_col,
    }


def _build_repeat_cell_request(
    ws,
    start_row: int,
    end_row: int,
    start_col: int,
    end_col: int,
    *,
    cell: dict[str, Any],
    fields: str,
) -> dict[str, Any]:
    return {
        "repeatCell": {
            "range": _sheet_range(ws, start_row, end_row, start_col, end_col),
            "cell": cell,
            "fields": fields,
        }
    }


def _build_range_background_request(
    ws,
    start_row: int,
    end_row: int,
    start_col: int,
    end_col: int,
    color: dict[str, float],
) -> dict[str, Any]:
    return _build_repeat_cell_request(
        ws,
        start_row,
        end_row,
        start_col,
        end_col,
        cell={"userEnteredFormat": {"backgroundColor": color}},
        fields="userEnteredFormat.backgroundColor",
    )


def _build_merge_request(ws, start_row: int, end_row: int, start_col: int, end_col: int) -> dict[str, Any] | None:
    if end_col <= start_col:
        return None
    return {
        "mergeCells": {
            "range": _sheet_range(ws, start_row, end_row, start_col, end_col),
            "mergeType": "MERGE_ALL",
        }
    }


def _build_unmerge_request(ws, start_row: int, end_row: int, start_col: int, end_col: int) -> dict[str, Any] | None:
    if end_col <= start_col:
        return None
    return {
        "unmergeCells": {
            "range": _sheet_range(ws, start_row, end_row, start_col, end_col),
        }
    }


def _build_update_cells_request(
    ws,
    start_row: int,
    start_col: int,
    values: list[list[str]],
) -> dict[str, Any]:
    row_count = len(values)
    col_count = max((len(row) for row in values), default=0)
    rows = []
    for row in values:
        rows.append(
            {
                "values": [
                    {"userEnteredValue": {"stringValue": str(cell or "")}}
                    for cell in row
                ]
            }
        )
    return {
        "updateCells": {
            "range": {
                "sheetId": ws.id,
                "startRowIndex": start_row - 1,
                "endRowIndex": start_row - 1 + row_count,
                "startColumnIndex": start_col - 1,
                "endColumnIndex": start_col - 1 + col_count,
            },
            "rows": rows,
            "fields": "userEnteredValue",
        }
    }


def _build_border_request(
    ws,
    start_row: int,
    end_row: int,
    start_col: int,
    end_col: int,
    *,
    top: dict[str, Any] | None = None,
    bottom: dict[str, Any] | None = None,
    left: dict[str, Any] | None = None,
    right: dict[str, Any] | None = None,
    inner_horizontal: dict[str, Any] | None = None,
    inner_vertical: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "range": _sheet_range(ws, start_row, end_row, start_col, end_col),
    }
    if top is not None:
        payload["top"] = dict(top)
    if bottom is not None:
        payload["bottom"] = dict(bottom)
    if left is not None:
        payload["left"] = dict(left)
    if right is not None:
        payload["right"] = dict(right)
    if inner_horizontal is not None:
        payload["innerHorizontal"] = dict(inner_horizontal)
    if inner_vertical is not None:
        payload["innerVertical"] = dict(inner_vertical)
    return {"updateBorders": payload}


def _insert_blank_columns(ws, insert_col: int, width: int) -> None:
    _invalidate_column_width_cache(ws)
    if insert_col > ws.col_count:
        ws.add_cols(width)
        return
    _batch_update(
        ws.spreadsheet,
        [
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
        ],
    )


def _insert_blank_columns_batch(ws, inserts: list[tuple[int, int]]) -> None:
    if not inserts:
        return
    _invalidate_column_width_cache(ws)
    requests: list[dict[str, Any]] = []
    append_width = 0
    for insert_col, width in sorted(inserts, key=lambda item: item[0], reverse=True):
        if width <= 0:
            continue
        if insert_col > ws.col_count:
            append_width += width
            continue
        requests.append(
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
        )
    if append_width > 0:
        ws.add_cols(append_width)
    _batch_update(ws.spreadsheet, requests)


def _ensure_blank_slot(ws, insert_col: int, width: int, header_rows: tuple[int, ...]) -> None:
    max_row = max(header_rows, default=1)
    if insert_col + width - 1 <= ws.col_count:
        range_values = ws.get(f"{rowcol_to_a1(1, insert_col)}:{rowcol_to_a1(max_row, insert_col + width - 1)}")
        is_blank = True
        for row_idx in header_rows:
            rel = row_idx - 1
            row = range_values[rel] if 0 <= rel < len(range_values) else []
            if any(_normalize_text(cell) for cell in row):
                is_blank = False
                break
        if is_blank:
            return
    _insert_blank_columns(ws, insert_col, width)


def _insert_columns_copy_template(
    ws,
    insert_col: int,
    width: int,
    template_start_col: int,
    used_last_row: int,
    copy_start_row: int = 1,
    copy_end_row: int | None = None,
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
    end_row = copy_end_row or used_last_row
    if end_row >= copy_start_row:
        requests.append(
            {
            "copyPaste": {
                "source": {
                    "sheetId": ws.id,
                    "startRowIndex": copy_start_row - 1,
                    "endRowIndex": end_row,
                    "startColumnIndex": source_start_col - 1,
                    "endColumnIndex": source_end_col,
                },
                "destination": {
                    "sheetId": ws.id,
                    "startRowIndex": copy_start_row - 1,
                    "endRowIndex": end_row,
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
    _batch_update(ws.spreadsheet, _build_column_width_requests(ws, start_col, widths))


def _build_column_width_requests(ws, start_col: int, widths: list[int]) -> list[dict[str, Any]]:
    if not widths:
        return []
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
    return requests


def _apply_border_box(
    ws,
    start_row: int,
    end_row: int,
    start_col: int,
    end_col: int,
    style: str = "SOLID",
) -> None:
    _batch_update(
        ws.spreadsheet,
        [
            _build_border_request(
                ws,
                start_row,
                end_row,
                start_col,
                end_col,
                top={"style": style},
                bottom={"style": style},
                left={"style": style},
                right={"style": style},
            )
        ],
    )


def _set_range_background(ws, start_row: int, end_row: int, start_col: int, end_col: int, color: dict[str, float]) -> None:
    _batch_update(
        ws.spreadsheet,
        [_build_range_background_request(ws, start_row, end_row, start_col, end_col, color)],
    )


def _set_group_borders(ws, start_row: int, end_row: int, start_col: int) -> None:
    requests: list[dict[str, Any]] = []
    for offset in (2, 6, 7):
        col = start_col + offset
        requests.append(_build_border_request(ws, start_row, end_row, col, col, left=BORDER_STYLE))
    _batch_update(ws.spreadsheet, requests)


def _merge_cells(ws, start_row: int, end_row: int, start_col: int, end_col: int) -> None:
    request = _build_merge_request(ws, start_row, end_row, start_col, end_col)
    if request:
        _batch_update(ws.spreadsheet, [request])


def _unmerge_cells(ws, start_row: int, end_row: int, start_col: int, end_col: int) -> None:
    request = _build_unmerge_request(ws, start_row, end_row, start_col, end_col)
    if request:
        _batch_update(ws.spreadsheet, [request])


def _unmerge_intersecting_merges(ws, start_row: int, end_row: int, start_col: int, end_col: int) -> None:
    if end_col <= start_col:
        return
    metadata = ws.spreadsheet.fetch_sheet_metadata(params={"fields": "sheets(properties(sheetId),merges)"})
    requests: list[dict[str, Any]] = []
    target_row_start = start_row - 1
    target_row_end = end_row
    target_col_start = start_col - 1
    target_col_end = end_col
    for sheet in metadata.get("sheets", []):
        props = sheet.get("properties", {})
        if props.get("sheetId") != ws.id:
            continue
        for merge in sheet.get("merges", []):
            merge_row_start = int(merge.get("startRowIndex", 0))
            merge_row_end = int(merge.get("endRowIndex", 0))
            merge_col_start = int(merge.get("startColumnIndex", 0))
            merge_col_end = int(merge.get("endColumnIndex", 0))
            row_intersects = merge_row_start < target_row_end and merge_row_end > target_row_start
            col_intersects = merge_col_start < target_col_end and merge_col_end > target_col_start
            if not (row_intersects and col_intersects):
                continue
            requests.append({"unmergeCells": {"range": merge}})
        break
    _batch_update(ws.spreadsheet, requests)


def _format_template_data_rows(ws, start_row: int, end_row: int, start_col: int, end_col: int) -> None:
    if end_row < start_row:
        return
    requests: list[dict[str, Any]] = []
    for row in range(start_row, end_row + 1):
        color = DATA_BG_ODD if row % 2 else DATA_BG_EVEN
        requests.append(
            {
                "repeatCell": {
                    "range": {
                        "sheetId": ws.id,
                        "startRowIndex": row - 1,
                        "endRowIndex": row,
                        "startColumnIndex": start_col - 1,
                        "endColumnIndex": end_col,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": color,
                        }
                    },
                    "fields": "userEnteredFormat.backgroundColor",
                }
            }
        )
    _batch_update(ws.spreadsheet, requests)


def _get_merged_profile_from_row_values(values: list[str], start_col: int) -> dict[str, Any]:
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


def _build_data_row_background_requests(
    ws,
    start_row: int,
    end_row: int,
    start_col: int,
    end_col: int,
) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    for row in range(start_row, end_row + 1):
        color = DATA_BG_ODD if row % 2 else DATA_BG_EVEN
        requests.append(_build_range_background_request(ws, row, row, start_col, end_col, color))
    return requests


def _append_fixed_width_group_divider_requests(
    ws,
    requests: list[dict[str, Any]],
    *,
    start_row: int,
    end_row: int,
    start_col: int,
) -> None:
    if end_row < start_row:
        return
    for offset in (3, 7, 8):
        col = start_col + offset
        requests.append(
            _build_border_request(
                ws,
                start_row,
                end_row,
                col,
                col,
                left=BORDER_INNER_STYLE,
            )
        )


def _append_fixed_width_template_requests(
    ws,
    requests: list[dict[str, Any]],
    *,
    start_col: int,
    title: str,
    title_row: int,
    group_row: int,
    data_start_row: int,
    end_row: int,
    footer_merge_row: int | None = None,
    include_top_border: bool = True,
) -> None:
    end_col = start_col + TEMPLATE_BLOCK_WIDTH - 1
    requests.extend(_build_column_width_requests(ws, start_col, TEMPLATE_COLUMN_WIDTHS))
    requests.append(_build_range_background_request(ws, title_row, group_row, start_col, end_col, HEADER_BG))
    requests.append(
        _build_update_cells_request(
            ws,
            title_row,
            start_col,
            [
                [title] + [""] * (TEMPLATE_BLOCK_WIDTH - 1),
                list(STANDARD_ROW2_VALUES),
            ],
        )
    )
    title_merge = _build_merge_request(ws, title_row, title_row, start_col, end_col)
    if title_merge:
        requests.append(title_merge)
    for offset, width, _label in TEMPLATE_GROUPS:
        merge_request = _build_merge_request(
            ws,
            group_row,
            group_row,
            start_col + offset,
            start_col + offset + width - 1,
        )
        if merge_request:
            requests.append(merge_request)
    divider_end_row = end_row if footer_merge_row is None else footer_merge_row - 1
    _append_fixed_width_group_divider_requests(
        ws,
        requests,
        start_row=title_row,
        end_row=divider_end_row,
        start_col=start_col,
    )
    if footer_merge_row is not None:
        footer_merge_request = _build_merge_request(ws, footer_merge_row, footer_merge_row, start_col, end_col)
        if footer_merge_request:
            requests.append(footer_merge_request)
        requests.append(
            _build_update_cells_request(
                ws,
                footer_merge_row,
                start_col,
                [[""] + [""] * (TEMPLATE_BLOCK_WIDTH - 1)],
            )
        )
    requests.append(
        _build_border_request(
            ws,
            title_row,
            end_row,
            start_col,
            end_col,
            top=BORDER_OUTER_STYLE if include_top_border else None,
            bottom=BORDER_OUTER_STYLE,
            left=BORDER_OUTER_STYLE,
            right=BORDER_OUTER_STYLE,
        )
    )


def _build_owner_ranges(groups: list[dict[str, int]], owners: list[str]) -> dict[str, dict[str, int]]:
    ranges: dict[str, dict[str, int]] = {}
    for group, owner in zip(groups, owners):
        key = _normalize_text(owner)
        if not key:
            continue
        start = int(group["start"])
        end = int(group["end"])
        existing = ranges.get(key)
        if existing is None:
            ranges[key] = {"start": start, "end": end}
            continue
        existing["start"] = min(existing["start"], start)
        existing["end"] = max(existing["end"], end)
    return ranges


def _append_training_change_element_group_requests(
    ws,
    requests: list[dict[str, Any]],
    *,
    original_ranges: dict[str, dict[str, int]],
    final_ranges: dict[str, dict[str, int]],
    affected_elements: set[str],
) -> None:
    for element in sorted(affected_elements):
        original = original_ranges.get(element)
        final = final_ranges.get(element)
        if original is None:
            raise ValueError(f"角色練度_變化 找不到既有屬性群組 element={element}")
        if final is None:
            raise ValueError(f"角色練度_變化 找不到插入後屬性群組 element={element}")
        unmerge_request = _build_unmerge_request(ws, 1, 1, int(original['start']), int(original['end']))
        if unmerge_request:
            requests.append(unmerge_request)
        requests.append(
            _build_range_background_request(
                ws,
                1,
                1,
                int(final["start"]),
                int(final["end"]),
                HEADER_BG,
            )
        )
        requests.append(
            _build_update_cells_request(
                ws,
                1,
                int(final["start"]),
                [[element] + [""] * (int(final["end"]) - int(final["start"]))],
            )
        )
        merge_request = _build_merge_request(ws, 1, 1, int(final["start"]), int(final["end"]))
        if merge_request:
            requests.append(merge_request)


def _set_training_element_template(ws, start_col: int, name: str, end_row: int) -> None:
    _set_column_widths(ws, start_col, TEMPLATE_COLUMN_WIDTHS)
    _unmerge_intersecting_merges(ws, 1, 2, start_col, start_col + TEMPLATE_BLOCK_WIDTH - 1)
    _set_range_background(ws, 1, 2, start_col, start_col + TEMPLATE_BLOCK_WIDTH - 1, HEADER_BG)
    _merge_cells(ws, 1, 1, start_col, start_col + TEMPLATE_BLOCK_WIDTH - 1)
    for offset, width, _label in TEMPLATE_GROUPS:
        if width > 1:
            _merge_cells(ws, 2, 2, start_col + offset, start_col + offset + width - 1)
    values = [
        [name] + [""] * (TEMPLATE_BLOCK_WIDTH - 1),
        [""] * TEMPLATE_BLOCK_WIDTH,
    ]
    values[1][0] = "技能"
    values[1][3] = "裝備(頭/手/身/腳)"
    values[1][7] = "收藏品"
    values[1][8] = "詞條(優/攻/彈)"
    ws.update(
        f"{rowcol_to_a1(1, start_col)}:{rowcol_to_a1(2, start_col + TEMPLATE_BLOCK_WIDTH - 1)}",
        values,
    )
    _format_template_data_rows(ws, 3, end_row, start_col, start_col + TEMPLATE_BLOCK_WIDTH - 1)
    _apply_border_box(ws, 1, end_row, start_col, start_col + TEMPLATE_BLOCK_WIDTH - 1)
    _set_group_borders(ws, 1, end_row, start_col)


def _set_training_change_template(ws, start_col: int, name: str, end_row: int) -> None:
    _set_column_widths(ws, start_col, TEMPLATE_COLUMN_WIDTHS)
    _unmerge_intersecting_merges(ws, 2, 3, start_col, start_col + TEMPLATE_BLOCK_WIDTH - 1)
    _set_range_background(ws, 2, 3, start_col, start_col + TEMPLATE_BLOCK_WIDTH - 1, HEADER_BG)
    _merge_cells(ws, 2, 2, start_col, start_col + TEMPLATE_BLOCK_WIDTH - 1)
    for offset, width, _label in TEMPLATE_GROUPS:
        if width > 1:
            _merge_cells(ws, 3, 3, start_col + offset, start_col + offset + width - 1)
    values = [
        [name] + [""] * (TEMPLATE_BLOCK_WIDTH - 1),
        [""] * TEMPLATE_BLOCK_WIDTH,
    ]
    values[1][0] = "技能"
    values[1][3] = "裝備(頭/手/身/腳)"
    values[1][7] = "收藏品"
    values[1][8] = "詞條(優/攻/彈)"
    ws.update(
        f"{rowcol_to_a1(2, start_col)}:{rowcol_to_a1(3, start_col + TEMPLATE_BLOCK_WIDTH - 1)}",
        values,
    )
    _format_template_data_rows(ws, 4, end_row, start_col, start_col + TEMPLATE_BLOCK_WIDTH - 1)
    _apply_border_box(ws, 2, end_row, start_col, start_col + TEMPLATE_BLOCK_WIDTH - 1)
    _set_group_borders(ws, 2, end_row, start_col)


def _set_standard_template(ws, start_col: int, name: str, end_row: int) -> None:
    _set_column_widths(ws, start_col, TEMPLATE_COLUMN_WIDTHS)
    _unmerge_intersecting_merges(ws, 1, 2, start_col, start_col + TEMPLATE_BLOCK_WIDTH - 1)
    _set_range_background(ws, 1, 2, start_col, start_col + TEMPLATE_BLOCK_WIDTH - 1, HEADER_BG)
    _merge_cells(ws, 1, 1, start_col, start_col + TEMPLATE_BLOCK_WIDTH - 1)
    for offset, width, _label in TEMPLATE_GROUPS:
        if width > 1:
            _merge_cells(ws, 2, 2, start_col + offset, start_col + offset + width - 1)
    values = [
        [name] + [""] * (TEMPLATE_BLOCK_WIDTH - 1),
        [""] * TEMPLATE_BLOCK_WIDTH,
    ]
    values[1][0] = "技能"
    values[1][3] = "裝備(頭/手/身/腳)"
    values[1][7] = "收藏品"
    values[1][8] = "詞條(優/攻/彈)"
    ws.update(
        f"{rowcol_to_a1(1, start_col)}:{rowcol_to_a1(2, start_col + TEMPLATE_BLOCK_WIDTH - 1)}",
        values,
    )
    _format_template_data_rows(ws, 3, end_row, start_col, start_col + TEMPLATE_BLOCK_WIDTH - 1)
    _apply_border_box(ws, 1, end_row, start_col, start_col + TEMPLATE_BLOCK_WIDTH - 1)
    _set_group_borders(ws, 1, end_row, start_col)


def _pick_template_group(profile: dict[str, Any], preferred_labels: tuple[str, ...] = ()) -> dict[str, int] | None:
    groups = profile.get("groups") or []
    labels = [_normalize_text(label) for label in profile.get("labels") or []]
    for preferred in preferred_labels:
        if preferred in labels:
            return groups[labels.index(preferred)]
    if groups:
        return groups[-1]
    return None


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
    copy_start_row: int = 1,
    copy_end_row: int | None = None,
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
                        "startRowIndex": copy_start_row - 1,
                        "endRowIndex": copy_end_row or used_last_row,
                        "startColumnIndex": template_start_col - 1,
                        "endColumnIndex": template_start_col - 1 + width,
                    },
                    "destination": {
                        "sheetId": ws.id,
                        "startRowIndex": copy_start_row - 1,
                        "endRowIndex": copy_end_row or used_last_row,
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
        profile = _get_fixed_width_profile(ws, 1, 2, expected_width, (1, 2))
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
    profile = _get_fixed_width_profile(ws, 1, 2, TEMPLATE_BLOCK_WIDTH, (1, 2))
    for group in profile["groups"]:
        if int(group["width"]) != TEMPLATE_BLOCK_WIDTH:
            continue
        _set_column_widths(ws, int(group["start"]), TEMPLATE_COLUMN_WIDTHS)


def _trim_training_change_trailing_columns(ws, expected_width: int) -> None:
    while True:
        profile = _get_fixed_width_profile(ws, 2, 2, expected_width, (1, 2, 3))
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
    profile = _get_fixed_width_profile(ws, 2, 2, TEMPLATE_BLOCK_WIDTH, (1, 2, 3))
    for group in profile["groups"]:
        if int(group["width"]) != TEMPLATE_BLOCK_WIDTH:
            continue
        _set_column_widths(ws, int(group["start"]), TEMPLATE_COLUMN_WIDTHS)


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
        if not _normalize_text(full_row[3]):
            full_row[3] = name
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


def _get_fixed_width_profile_from_row_values(
    row_values: dict[int, list[str]],
    label_row: int,
    start_col: int,
    width: int,
    extent_rows: tuple[int, ...],
) -> dict[str, Any]:
    end_col = max((_last_nonempty_col(row_values.get(row, [])) for row in extent_rows), default=0)
    if end_col < start_col:
        return {"groups": [], "labels": [], "end_col": start_col - 1, "values": []}
    block_count = ((end_col - start_col + 1) + width - 1) // width
    groups: list[dict[str, int]] = []
    labels: list[str] = []
    label_values = row_values.get(label_row, [])
    for idx in range(block_count):
        block_start = start_col + idx * width
        block_end = block_start + width - 1
        groups.append({"start": block_start, "end": block_end, "width": width})
        label_idx = block_start - 1
        label = label_values[label_idx] if 0 <= label_idx < len(label_values) else ""
        labels.append(_normalize_text(label))
    return {
        "groups": groups,
        "labels": labels,
        "end_col": start_col + block_count * width - 1,
        "values": [],
    }


def _raise_previous_not_found(
    sheet_name: str,
    name: str,
    previous: str,
    *,
    member_type: str = "",
    element: str = "",
) -> None:
    details = [f"sheet={sheet_name}", f"name={name}", f"previous={previous}"]
    if member_type:
        details.append(f"type={member_type}")
    if element:
        details.append(f"element={element}")
    raise ValueError("找不到前位角色: " + ", ".join(details))


def _insert_fixed_width_group_state(
    groups: list[dict[str, int]],
    labels: list[str],
    insert_col: int,
    width: int,
    name: str,
) -> tuple[list[dict[str, int]], list[str]]:
    shifted_groups: list[dict[str, int]] = []
    insert_idx = len(groups)
    for idx, group in enumerate(groups):
        start = int(group["start"])
        end = int(group["end"])
        if start >= insert_col and insert_idx == len(groups):
            insert_idx = idx
        if start >= insert_col:
            start += width
            end += width
        shifted_groups.append({"start": start, "end": end, "width": int(group["width"])})
    new_group = {"start": insert_col, "end": insert_col + width - 1, "width": width}
    shifted_groups.insert(insert_idx, new_group)
    shifted_labels = list(labels)
    shifted_labels.insert(insert_idx, name)
    return shifted_groups, shifted_labels


def _build_missing_insertions(desired_names: list[str], existing_names: list[str]) -> list[dict[str, str | None]]:
    seen = {_normalize_text(name) for name in existing_names if _normalize_text(name)}
    normalized_desired: list[str] = []
    for value in desired_names:
        name = _normalize_text(value)
        if not name or name in normalized_desired:
            continue
        normalized_desired.append(name)

    actions: list[dict[str, str | None]] = []
    for idx, name in enumerate(normalized_desired):
        if name in seen:
            continue
        previous = normalized_desired[idx - 1] if idx > 0 else None
        actions.append({"name": name, "previous": previous})
        seen.add(name)
    return actions


def _build_fixed_width_insert_actions(
    desired_names: list[str],
    existing_names: list[str],
    existing_groups: list[dict[str, int]],
    group_start_col: int,
    *,
    sheet_name: str,
    member_type: str = "",
    element: str = "",
) -> list[dict[str, Any]]:
    labels = [_normalize_text(label) for label in existing_names]
    groups = [
        {"start": int(group["start"]), "end": int(group["end"]), "width": int(group["width"])}
        for group in existing_groups
    ]
    actions: list[dict[str, Any]] = []
    for action in _build_missing_insertions(desired_names, labels):
        name = str(action["name"] or "").strip()
        previous = str(action["previous"] or "").strip()
        if previous:
            if previous not in labels:
                _raise_previous_not_found(sheet_name, name, previous, member_type=member_type, element=element)
            insert_col = int(groups[labels.index(previous)]["end"]) + 1
        else:
            insert_col = group_start_col
        actions.append({"name": name, "previous": previous or None, "insert_col": insert_col})
        groups, labels = _insert_fixed_width_group_state(groups, labels, insert_col, TEMPLATE_BLOCK_WIDTH, name)
    return actions


def _build_structural_sync_plan(entries: list[dict[str, str]]) -> dict[str, Any]:
    names_by_element: dict[str, list[str]] = defaultdict(list)
    score_entries_by_key: dict[str, list[dict[str, str]]] = defaultdict(list)
    seen_names_by_element: dict[str, set[str]] = defaultdict(set)
    seen_score_labels_by_key: dict[str, set[str]] = defaultdict(set)
    name_to_element: dict[str, str] = {}

    for row in entries:
        name = _normalize_text(row.get("name"))
        element = _normalize_text(row.get("element"))
        member_type = _normalize_text(row.get("type"))
        nickname = _normalize_text(row.get("nickname")) or name

        if name and element and name not in seen_names_by_element[element]:
            seen_names_by_element[element].add(name)
            names_by_element[element].append(name)
        if name and element and name not in name_to_element:
            name_to_element[name] = element

        key = _build_group_key(member_type, element)
        if member_type and element and nickname and nickname not in seen_score_labels_by_key[key]:
            seen_score_labels_by_key[key].add(nickname)
            score_entries_by_key[key].append(
                {
                    "name": name,
                    "nickname": nickname,
                    "type": member_type,
                    "element": element,
                }
            )

    return {
        "names_by_element": dict(names_by_element),
        "score_entries_by_key": dict(score_entries_by_key),
        "name_to_element": name_to_element,
    }


def _sync_training_element_sheet(spreadsheet, element: str, desired_names: list[str]) -> int:
    if not desired_names:
        return 0
    ws = spreadsheet.worksheet(f"{TRAINING_SHEET_PREFIX}{element}")
    _trim_training_element_trailing_columns(ws, TEMPLATE_BLOCK_WIDTH)
    values = _get_used_values(ws)
    used_last_row = max(len(values), 3)
    row_values = {
        1: values[0] if len(values) >= 1 else [],
        2: values[1] if len(values) >= 2 else [],
    }
    profile = _get_fixed_width_profile_from_row_values(row_values, 1, 2, TEMPLATE_BLOCK_WIDTH, (1, 2))
    if not profile["groups"]:
        raise ValueError(f"角色練度_{element} 找不到可用的角色群組")

    actions = _build_fixed_width_insert_actions(
        desired_names,
        list(profile["labels"]),
        list(profile["groups"]),
        int(profile["groups"][0]["start"]),
        sheet_name=f"角色練度_{element}",
        element=element,
    )
    _insert_blank_columns_batch(ws, [(int(action["insert_col"]), TEMPLATE_BLOCK_WIDTH) for action in actions])
    requests: list[dict[str, Any]] = []
    for action in actions:
        _append_fixed_width_template_requests(
            ws,
            requests,
            start_col=int(action["insert_col"]),
            title=str(action["name"]),
            title_row=1,
            group_row=2,
            data_start_row=3,
            end_row=used_last_row,
        )
    _batch_update(ws.spreadsheet, requests)
    return len(actions)


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
    _trim_training_change_trailing_columns(ws, TEMPLATE_BLOCK_WIDTH)
    values = _get_used_values(ws)
    used_last_row = max(len(values), 4)
    row_values = {
        1: values[0] if len(values) >= 1 else [],
        2: values[1] if len(values) >= 2 else [],
        3: values[2] if len(values) >= 3 else [],
    }
    profile = _get_fixed_width_profile_from_row_values(row_values, 2, 2, TEMPLATE_BLOCK_WIDTH, (1, 2, 3))
    if not profile["groups"]:
        raise ValueError("變化表 找不到可用的角色群組")

    row1_segment = _pad_row(row_values.get(1, []), int(profile["end_col"]))[1 : int(profile["end_col"])]
    current_groups = [
        {"start": int(group["start"]), "end": int(group["end"]), "width": int(group["width"])}
        for group in profile["groups"]
    ]
    current_labels = [_normalize_text(label) for label in profile["labels"]]
    current_owners = [_group_label_by_col(row1_segment, 2, int(group["start"])) for group in current_groups]
    original_element_ranges = _build_owner_ranges(current_groups, current_owners)
    all_actions: list[dict[str, Any]] = []
    affected_elements: set[str] = set()

    for element in HEALTHCHECK_ELEMENT_ORDER:
        desired_names = desired_by_element.get(element) or []
        if not desired_names:
            continue
        element_groups: list[dict[str, int]] = []
        element_names: list[str] = []
        for idx, group in enumerate(current_groups):
            if current_owners[idx] != element:
                continue
            element_groups.append(group)
            element_names.append(current_labels[idx])
        if not element_groups:
            raise ValueError(f"變化表 找不到屬性群組: element={element}")

        actions = _build_fixed_width_insert_actions(
            desired_names,
            element_names,
            element_groups,
            int(element_groups[0]["start"]),
            sheet_name="變化表",
            element=element,
        )
        for action in actions:
            insert_col = int(action["insert_col"])
            all_actions.append({"insert_col": insert_col, "name": str(action["name"]), "element": element})
            current_groups, current_labels = _insert_fixed_width_group_state(
                current_groups,
                current_labels,
                insert_col,
                TEMPLATE_BLOCK_WIDTH,
                str(action["name"]),
            )
            current_owners.insert(current_labels.index(str(action["name"])), element)
            affected_elements.add(element)
    _insert_blank_columns_batch(ws, [(int(action["insert_col"]), TEMPLATE_BLOCK_WIDTH) for action in all_actions])
    requests: list[dict[str, Any]] = []
    for action in all_actions:
        _append_fixed_width_template_requests(
            ws,
            requests,
            start_col=int(action["insert_col"]),
            title=str(action["name"]),
            title_row=2,
            group_row=3,
            data_start_row=4,
            end_row=used_last_row,
            include_top_border=False,
        )
    _append_training_change_element_group_requests(
        ws,
        requests,
        original_ranges=original_element_ranges,
        final_ranges=_build_owner_ranges(current_groups, current_owners),
        affected_elements=affected_elements,
    )
    _batch_update(ws.spreadsheet, requests)
    return len(all_actions)


def _set_standard_column_widths(ws, start_col: int) -> None:
    _set_column_widths(ws, start_col, TEMPLATE_COLUMN_WIDTHS)


def _repair_standard_column_widths(ws) -> None:
    profile = _get_fixed_width_profile(ws, 1, 2, TEMPLATE_BLOCK_WIDTH, (1, 2))
    for group in profile["groups"]:
        if int(group["width"]) != TEMPLATE_BLOCK_WIDTH:
            continue
        _set_standard_column_widths(ws, int(group["start"]))


def _sync_standard_sheet(spreadsheet, desired_by_element: dict[str, list[str]], name_to_element: dict[str, str]) -> int:
    ws = spreadsheet.worksheet(STANDARD_WORKSHEET)
    values = _get_used_values(ws)
    used_last_row = max(len(values), 5)
    row_values = {
        1: values[0] if len(values) >= 1 else [],
        2: values[1] if len(values) >= 2 else [],
    }
    profile = _get_fixed_width_profile_from_row_values(row_values, 1, 2, TEMPLATE_BLOCK_WIDTH, (1, 2))
    current_groups = [
        {"start": int(group["start"]), "end": int(group["end"]), "width": int(group["width"])}
        for group in profile["groups"]
    ]
    current_labels = [_normalize_text(label) for label in profile["labels"]]
    all_actions: list[dict[str, Any]] = []

    for element in HEALTHCHECK_ELEMENT_ORDER:
        desired_names = desired_by_element.get(element) or []
        if not desired_names:
            continue
        element_groups: list[dict[str, int]] = []
        element_names: list[str] = []
        for idx, label in enumerate(current_labels):
            if name_to_element.get(label) != element:
                continue
            element_groups.append(current_groups[idx])
            element_names.append(label)
        if not element_groups:
            raise ValueError(f"練度標準表 找不到屬性群組: element={element}")

        actions = _build_fixed_width_insert_actions(
            desired_names,
            element_names,
            element_groups,
            int(element_groups[0]["start"]),
            sheet_name="練度標準表",
            element=element,
        )
        for action in actions:
            insert_col = int(action["insert_col"])
            all_actions.append({"insert_col": insert_col, "name": str(action["name"])})
            current_groups, current_labels = _insert_fixed_width_group_state(
                current_groups,
                current_labels,
                insert_col,
                TEMPLATE_BLOCK_WIDTH,
                str(action["name"]),
            )
    _insert_blank_columns_batch(ws, [(int(action["insert_col"]), TEMPLATE_BLOCK_WIDTH) for action in all_actions])
    requests: list[dict[str, Any]] = []
    for action in all_actions:
        _append_fixed_width_template_requests(
            ws,
            requests,
            start_col=int(action["insert_col"]),
            title=str(action["name"]),
            title_row=1,
            group_row=2,
            data_start_row=3,
            end_row=used_last_row,
            footer_merge_row=5,
        )
    _batch_update(ws.spreadsheet, requests)
    return len(all_actions)


def _build_score_result_blocks_from_values(values: list[list[str]]) -> dict[str, dict[str, Any]]:
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


def _build_score_result_blocks(ws) -> dict[str, dict[str, Any]]:
    return _build_score_result_blocks_from_values(_get_used_values(ws))


def _append_score_result_column_requests(
    ws,
    requests: list[dict[str, Any]],
    *,
    insert_col: int,
    nickname: str,
    used_last_row: int,
) -> None:
    values = [[nickname]]
    for _row in range(4, used_last_row + 1):
        values.append([""])
    requests.append(_build_update_cells_request(ws, 3, insert_col, values))
    requests.append(
        _build_repeat_cell_request(
            ws,
            3,
            used_last_row,
            insert_col,
            insert_col,
            cell={"userEnteredFormat": {"textFormat": {"bold": False}}},
            fields="userEnteredFormat.textFormat.bold",
        )
    )


def _insert_score_result_state(
    char_cols: list[dict[str, Any]],
    insert_col: int,
    label: str,
    total_col: int | None,
    rank_col: int | None,
) -> tuple[list[dict[str, Any]], int | None, int | None]:
    updated_cols: list[dict[str, Any]] = []
    insert_idx = len(char_cols)
    for idx, item in enumerate(char_cols):
        col = int(item["col"])
        if col >= insert_col and insert_idx == len(char_cols):
            insert_idx = idx
        if col >= insert_col:
            col += 1
        updated_cols.append({"col": col, "label": item["label"]})
    updated_cols.insert(insert_idx, {"col": insert_col, "label": label})
    next_total = None if total_col is None else int(total_col) + (1 if int(total_col) >= insert_col else 0)
    next_rank = None if rank_col is None else int(rank_col) + (1 if int(rank_col) >= insert_col else 0)
    return updated_cols, next_total, next_rank


def _shift_score_result_block_after_insert(block: dict[str, Any], insert_col: int) -> None:
    shifted_cols: list[dict[str, Any]] = []
    for item in block.get("char_cols", []):
        col = int(item["col"])
        if col >= insert_col:
            col += 1
        shifted_cols.append({"col": col, "label": item["label"]})
    block["char_cols"] = shifted_cols
    if block.get("total_col") is not None and int(block["total_col"]) >= insert_col:
        block["total_col"] = int(block["total_col"]) + 1
    if block.get("rank_col") is not None and int(block["rank_col"]) >= insert_col:
        block["rank_col"] = int(block["rank_col"]) + 1


def _build_score_result_insert_actions(
    desired_entries: list[dict[str, str]],
    char_cols: list[dict[str, Any]],
    total_col: int | None,
    rank_col: int | None,
    *,
    sheet_name: str,
    member_type: str,
    element: str,
) -> list[dict[str, Any]]:
    current_cols = [{"col": int(item["col"]), "label": _normalize_text(item["label"])} for item in char_cols]
    current_labels = [_normalize_label(item["label"]) for item in current_cols]
    desired_labels: list[str] = []
    for entry in desired_entries:
        label = _normalize_text(entry.get("nickname") or entry.get("name"))
        if label and label not in desired_labels:
            desired_labels.append(label)

    actions: list[dict[str, Any]] = []
    for action in _build_missing_insertions(desired_labels, [item["label"] for item in current_cols]):
        label = str(action["name"] or "").strip()
        previous = str(action["previous"] or "").strip()
        if previous:
            normalized_previous = _normalize_label(previous)
            if normalized_previous not in current_labels:
                _raise_previous_not_found(sheet_name, label, previous, member_type=member_type, element=element)
            insert_col = int(current_cols[current_labels.index(normalized_previous)]["col"]) + 1
        else:
            insert_col = int(current_cols[0]["col"]) if current_cols else int(total_col or rank_col or 4)
        template_col = int(current_cols[0]["col"]) if current_cols else max(1, insert_col - 1)
        actions.append(
            {
                "label": label,
                "previous": previous or None,
                "insert_col": insert_col,
                "template_col": template_col,
            }
        )
        current_cols, total_col, rank_col = _insert_score_result_state(
            current_cols,
            insert_col,
            label,
            total_col,
            rank_col,
        )
        current_labels = [_normalize_label(item["label"]) for item in current_cols]
    return actions


def _sync_score_result_columns(spreadsheet, entries_by_key: dict[str, list[dict[str, str]]]) -> int:
    ws = spreadsheet.worksheet(SCORE_RESULT_WORKSHEET)
    values = _get_used_values(ws)
    used_last_row = max(len(values), 3)
    blocks = _build_score_result_blocks_from_values(values)
    inserted = 0
    for member_type in HEALTHCHECK_TYPE_ORDER:
        for element in HEALTHCHECK_ELEMENT_ORDER:
            key = _build_group_key(member_type, element)
            desired_entries = entries_by_key.get(key) or []
            if not desired_entries:
                continue
            block = blocks.get(key)
            if not block:
                raise ValueError(f"練度評分結果找不到區塊: type={member_type} element={element}")
            actions = _build_score_result_insert_actions(
                desired_entries,
                list(block["char_cols"]),
                block.get("total_col"),
                block.get("rank_col"),
                sheet_name="練度評分結果",
                member_type=member_type,
                element=element,
            )
            _insert_blank_columns_batch(ws, [(int(action["insert_col"]), 1) for action in actions])
            requests: list[dict[str, Any]] = []
            for action in actions:
                _append_score_result_column_requests(
                    ws,
                    requests,
                    insert_col=int(action["insert_col"]),
                    nickname=str(action["label"]),
                    used_last_row=used_last_row,
                )
                for block_key, other_block in blocks.items():
                    if block_key == key:
                        continue
                    _shift_score_result_block_after_insert(other_block, int(action["insert_col"]))
                block["char_cols"], block["total_col"], block["rank_col"] = _insert_score_result_state(
                    list(block["char_cols"]),
                    int(action["insert_col"]),
                    str(action["label"]),
                    block.get("total_col"),
                    block.get("rank_col"),
                )
                inserted += 1
            _batch_update(ws.spreadsheet, requests)
    return inserted


def _sync_all_structural_sheets(spreadsheet, entries: list[dict[str, str]]) -> dict[str, int]:
    plan = _build_structural_sync_plan(entries)
    desired_by_element = plan["names_by_element"]
    score_entries_by_key = plan["score_entries_by_key"]
    name_to_element = plan["name_to_element"]

    stats = {
        "training_element_columns": 0,
        "training_change_columns": 0,
        "standard_columns": 0,
        "score_result_columns": 0,
    }
    for element, desired_names in desired_by_element.items():
        stats["training_element_columns"] += _sync_training_element_sheet(spreadsheet, element, desired_names)
    stats["training_change_columns"] = _sync_training_change_sheet(spreadsheet, desired_by_element)
    stats["standard_columns"] = _sync_standard_sheet(spreadsheet, desired_by_element, name_to_element)
    stats["score_result_columns"] = _sync_score_result_columns(spreadsheet, score_entries_by_key)
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
