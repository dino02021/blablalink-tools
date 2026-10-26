# -*- coding: utf-8 -*-
"""妮姬資料上傳流程與共用試算表 helper。"""

from __future__ import annotations

import json
import math
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional

import requests

from ...jobs.nikke_upload import run_nikke_upload
from ...constants import CHAR_DETAILS_DIR, CHAR_DETAILS_TYPE, WEB_APP_URL
from ...google_sheets.healthcheck import normalize_label
from ...log import log_exc, log_msg

def open_upload_nikke_dialog(app) -> None:
    """由主畫面呼叫：直接整批上傳目前角色資料。"""
    if not getattr(app, "_last_slug", None):
        app._set_nikke_status("請先登入", "red")
        return
    app.nikke_status_var.set("上傳中...")
    app.nikke_status_label.configure(foreground="green")
    app.upload_nikke_btn.config(state="disabled")
    threading.Thread(target=_upload_worker, args=(app,), daemon=True).start()


def _upload_worker(app) -> None:
    try:
        result = run_nikke_upload(account=app._last_slug or "")
        ok = result.ok
        msg = result.message
        ok_count = int(result.stats.get("ok_count", 0) or 0)
        total_count = int(result.stats.get("total_count", 0) or 0)
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        ok = False
        msg = str(exc)
        ok_count = 0
        total_count = 0
    app.root.after(0, lambda: _finish_upload(app, ok, ok_count, total_count, msg))


def _finish_upload(app, ok: bool, ok_count: int, total_count: int, msg: str) -> None:
    app.upload_nikke_btn.config(state="normal")
    if ok:
        app.nikke_status_var.set(f"上傳成功 ({ok_count}/{total_count})")
        app.nikke_status_label.configure(foreground="green")
        return
    app.nikke_status_var.set(f"上傳失敗：{msg or '未知錯誤'}")
    app.nikke_status_label.configure(foreground="red")


def _collect_character_sheets(worksheets) -> List[Any]:
    titles = {"角色練度_電擊", "角色練度_水冷", "角色練度_燃燒", "角色練度_風壓", "角色練度_鐵甲"}
    return [ws for ws in worksheets if ws.title in titles]


def _find_change_sheet(worksheets) -> Any | None:
    for ws in worksheets:
        if ws.title == "角色練度_變化":
            return ws
    return None


def _find_member_sheet(worksheets) -> Any | None:
    for ws in worksheets:
        if ws.title == "成員名稱":
            return ws
    return None


def _load_change_headers(change_sheet, values_by_ws=None) -> Tuple[Dict[str, int], Dict[str, List[int]]]:
    if change_sheet is None:
        return {}, {}
    nick_col = _source_column(change_sheet, 1, values_by_ws)
    nick_to_row = _unique_rows(nick_col, 4, change_sheet.title)
    hdrs = _source_row(change_sheet, 2, values_by_ws)
    pos_map: Dict[str, List[int]] = {}
    for idx, h in enumerate(hdrs, start=1):
        name = normalize_label(h)
        if not name:
            continue
        pos_map.setdefault(name, []).append(idx)
    return nick_to_row, pos_map


def _load_sheet_headers(
    main_sheets,
    member_sheet: Any | None = None,
    values_by_ws=None,
) -> Tuple[Dict[Any, Dict[str, int]], Dict[str, str], Dict[Any, Dict[str, int]], Dict[Any, Dict[str, List[int]]]]:
    member_id_to_nick: Dict[str, str] = {}
    if member_sheet is not None:
        member_names = _source_column(member_sheet, 1, values_by_ws)
        member_ids = _source_column(member_sheet, 3, values_by_ws)
        _unique_rows(member_names, 2, member_sheet.title)
        for clean_id, row_idx in _unique_rows(member_ids, 2, member_sheet.title).items():
            name = str(member_names[row_idx - 1] if row_idx - 1 < len(member_names) else "").strip()
            if not name:
                raise ValueError(f"成員名稱表的 Blabla ID {clean_id} 缺少名稱")
            member_id_to_nick[clean_id] = name

    nick_to_row_by_ws: Dict[Any, Dict[str, int]] = {}
    member_id_to_row_by_ws: Dict[Any, Dict[str, int]] = {}
    header_pos: Dict[Any, Dict[str, List[int]]] = {}
    for ws in main_sheets:
        nick_col = _source_column(ws, 1, values_by_ws)
        start_row = 3 if ws.title.startswith("角色練度_") else 2
        nick_to_row_by_ws[ws] = _unique_rows(nick_col, start_row, ws.title)

        if member_sheet is None:
            member_id_col = _source_column(ws, 3, values_by_ws)
            member_id_to_row_by_ws[ws] = _unique_rows(member_id_col, start_row, ws.title)

        hdrs = _source_row(ws, 1, values_by_ws)
        pos_map: Dict[str, List[int]] = {}
        for idx, h in enumerate(hdrs, start=1):
            name = normalize_label(h)
            if not name:
                continue
            pos_map.setdefault(name, []).append(idx)
        header_pos[ws] = pos_map
    return nick_to_row_by_ws, member_id_to_nick, member_id_to_row_by_ws, header_pos


def _build_updates(
    details: List[dict],
    nickname: str,
    member_id: str,
    main_sheets,
    nick_to_row_by_ws: Dict[Any, Dict[str, int]],
    member_id_to_nick: Dict[str, str],
    member_id_to_row_by_ws: Dict[Any, Dict[str, int]],
    header_pos: Dict[Any, Dict[str, List[int]]],
    change_sheet: Any | None,
    change_nick_to_row: Dict[str, int],
    change_header_pos: Dict[str, List[int]],
) -> Tuple[Dict[Any, List[Tuple[int, int, List[Any]]]], List[Tuple[Any, int, int, List[Any], str, str]]]:
    updates_by_ws: Dict[Any, List[Tuple[int, int, List[Any]]]] = {}
    pending_changes: List[Tuple[Any, int, int, List[Any], str, str]] = []
    member_nick = member_id_to_nick.get(member_id, "") if member_id else ""
    for detail in details:
        name = normalize_label(detail.get("name"))
        if not name:
            continue
        target_count = 0
        for ws in main_sheets:
            start_cols = header_pos.get(ws, {}).get(name, [])
            if not start_cols:
                continue
            nick_to_row = nick_to_row_by_ws.get(ws, {})
            member_id_to_row = member_id_to_row_by_ws.get(ws, {})
            row_idx = None
            if member_id:
                if member_nick:
                    row_idx = nick_to_row.get(member_nick)
                if not row_idx:
                    row_idx = member_id_to_row.get(member_id)
            if not row_idx:
                row_idx = nick_to_row.get(nickname)
            if not row_idx:
                raise ValueError(f"{ws.title} 找不到成員 {nickname}（Blabla ID: {member_id or '-'}）")
            target_count += 1
            row_nick = next((key for key, value in nick_to_row.items() if value == row_idx), nickname)
            change_nick = next((candidate for candidate in (member_nick, row_nick, nickname) if candidate in change_nick_to_row), "")
            if change_sheet and (not change_nick or not change_header_pos.get(name)):
                raise ValueError(f"變化表找不到 {nickname} 的 {name} 資料位置")
            vals = _build_row_values(detail)
            for start_col in start_cols:
                updates_by_ws.setdefault(ws, []).append((row_idx, start_col, vals))
                if change_sheet:
                    pending_changes.append((ws, row_idx, start_col, vals, change_nick, name))
        if target_count == 0:
            raise ValueError(f"找不到角色 {name} 的練度欄位")

    return updates_by_ws, pending_changes


def _populate_change_updates(
    pending: List[Tuple[Any, int, int, List[Any], str, str]],
    combined_changes: Dict[Any, List[Tuple[int, int, List[Any]]]],
    change_sheet: Any | None,
    change_nick_to_row: Dict[str, int],
    change_header_pos: Dict[str, List[int]],
    values_by_ws=None,
) -> None:
    if not change_sheet or not pending:
        return
    from gspread.utils import rowcol_to_a1

    pending_by_ws: Dict[Any, List[Tuple[int, int, List[Any], str, str]]] = {}
    for ws, row_idx, start_col, vals, nickname, name in pending:
        pending_by_ws.setdefault(ws, []).append((row_idx, start_col, vals, nickname, name))

    for ws, items in pending_by_ws.items():
        ranges: List[str] = []
        change_ranges: List[str] = []
        for row_idx, start_col, vals, nickname, name in items:
            end_col = start_col + len(vals) - 1
            ranges.append(f"{rowcol_to_a1(row_idx, start_col)}:{rowcol_to_a1(row_idx, end_col)}")
            change_row = change_nick_to_row.get(nickname)
            change_cols = change_header_pos.get(name, [])
            if not change_row or not change_cols:
                continue
            for change_col in change_cols:
                change_ranges.append(
                    f"{rowcol_to_a1(change_row, change_col)}:"
                    f"{rowcol_to_a1(change_row, change_col + len(vals) - 1)}"
                )
        values = ([[_source_slice(ws, row, col, len(vals), values_by_ws)] for row, col, vals, _, _ in items]
                  if values_by_ws is not None else _batch_get_ranges(ws, ranges, 80))
        change_values: List[List[List[str]]] = []
        if change_ranges and values_by_ws is None:
            change_values = _batch_get_ranges(change_sheet, change_ranges, 80)
        for (row_idx, start_col, vals, nickname, name), old_block in zip(items, values):
            old_vals = old_block[0] if old_block else []
            diffs = _build_diff_values_from_slice(old_vals, vals)
            if not any(diffs):
                continue
            change_row = change_nick_to_row.get(nickname)
            change_cols = change_header_pos.get(name, [])
            if not change_row or not change_cols:
                raise ValueError(f"變化表找不到 {nickname} 的 {name} 資料位置")
            for change_col in change_cols:
                existing_vals = (_source_slice(change_sheet, change_row, change_col, len(vals), values_by_ws)
                                 if values_by_ws is not None else _get_change_existing(change_values, change_ranges, change_row, change_col, len(vals)))
                merged = _merge_change_values(existing_vals, diffs)
                if not any(merged):
                    continue
                combined_changes.setdefault(change_sheet, []).append((change_row, change_col, merged))


def _get_change_existing(
    change_values: List[List[List[str]]],
    change_ranges: List[str],
    row_idx: int,
    col_idx: int,
    length: int,
) -> List[str]:
    if not change_ranges or not change_values:
        return []
    try:
        from gspread.utils import rowcol_to_a1  # type: ignore
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return []
    rng = f"{rowcol_to_a1(row_idx, col_idx)}:{rowcol_to_a1(row_idx, col_idx + length - 1)}"
    try:
        pos = change_ranges.index(rng)
    except ValueError:
        return []
    block = change_values[pos] if pos < len(change_values) else []
    return block[0] if block else []


def _batch_get_ranges(sheet: Any, ranges: List[str], chunk_size: int) -> List[List[List[str]]]:
    if not ranges:
        return []
    out: List[List[List[str]]] = []
    total = len(ranges)
    for idx in range(0, total, chunk_size):
        chunk = ranges[idx : idx + chunk_size]
        values = sheet.batch_get(chunk)
        if len(values) != len(chunk):
            raise ValueError(f"無法完整讀取 {sheet.title} 的舊資料")
        out.extend(values)
    return out


def _merge_change_values(existing: List[str], diffs: List[Any]) -> List[Any]:
    out: List[Any] = []
    for idx, diff in enumerate(diffs):
        if diff == "":
            out.append(existing[idx] if idx < len(existing) else "")
            continue
        base_val = existing[idx] if idx < len(existing) else ""
        merged = _add_change_values(base_val, diff)
        out.append(merged)
    return out


def _add_change_values(existing: Any, diff: Any) -> str:
    diff_num = _parse_change_number(diff)
    if diff_num is None:
        return str(diff)
    base_num = _parse_change_number(existing)
    if base_num is None:
        return _format_change_number(diff_num)
    return _format_change_number(base_num + diff_num)


def _parse_change_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text == "":
        return None
    if text.startswith("+"):
        text = text[1:]
    try:
        return float(text)
    except Exception:
        return None


def _format_change_number(value: float) -> str:
    if value == 0:
        return "+0"
    if value.is_integer():
        return f"{value:+.0f}"
    return f"{value:+.2f}"


def _build_diff_values(old_vals: List[str], start_col: int, new_vals: List[Any]) -> List[Any]:
    diffs: List[Any] = []
    for idx, new_val in enumerate(new_vals):
        col_idx = start_col + idx
        old_val = old_vals[col_idx - 1] if col_idx - 1 < len(old_vals) else ""
        if old_val == "":
            diffs.append("")
            continue
        if idx == _favorite_index():
            diff_val = _calc_favorite_diff(old_val, new_val)
        else:
            diff_val = _calc_cell_diff(old_val, new_val)
        diffs.append(diff_val)
    return diffs


def _build_diff_values_from_slice(old_vals: List[str], new_vals: List[Any]) -> List[Any]:
    diffs: List[Any] = []
    for idx, new_val in enumerate(new_vals):
        old_val = old_vals[idx] if idx < len(old_vals) else ""
        if old_val == "":
            diffs.append("")
            continue
        if idx == _favorite_index():
            diff_val = _calc_favorite_diff(old_val, new_val)
        else:
            diff_val = _calc_cell_diff(old_val, new_val)
        diffs.append(diff_val)
    return diffs


def _favorite_index() -> int:
    return 8


def _calc_favorite_diff(old_val: Any, new_val: Any) -> str:
    if old_val == new_val:
        return ""
    old_score = _parse_favorite_value(old_val)
    new_score = _parse_favorite_value(new_val)
    if old_score is None or new_score is None:
        return _calc_cell_diff(old_val, new_val)
    diff = new_score - old_score
    if diff == 0:
        return ""
    return f"{diff:+d}"


def _parse_favorite_value(val: Any) -> int | None:
    if val is None:
        return None
    text = str(val).strip().upper()
    if not text:
        return None
    if text == "N":
        return -1
    if text.startswith("SR"):
        base = 15
        rest = text[2:]
    elif text.startswith("R"):
        base = 0
        rest = text[1:]
    else:
        return None
    if rest == "":
        lv = 0
    else:
        try:
            lv = int(rest)
        except Exception:
            return None
    return base + lv


def _calc_cell_diff(old_val: Any, new_val: Any) -> str:
    if old_val == new_val:
        return ""
    try:
        old_num = _coerce_diff_value(old_val)
        new_num = _coerce_diff_value(new_val)
        if old_num is None or new_num is None:
            return str(new_val)
        diff = new_num - old_num
        if diff == 0:
            return ""
        if diff.is_integer():
            return f"{diff:+.0f}"
        return f"{diff:+.2f}"
    except Exception:
        return str(new_val)


def _coerce_diff_value(val: Any) -> Optional[float]:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    text = str(val).strip().upper()
    if text == "":
        return None
    mapping = {
        "T9": -1,
        "R0": 1,
        "R1": 2,
        "R2": 3,
        "R3": 4,
        "R4": 5,
        "R5": 6,
        "R6": 7,
        "R7": 8,
        "R8": 9,
        "R9": 10,
        "R10": 11,
        "R11": 12,
        "R12": 13,
        "R13": 14,
        "R14": 15,
        "R15": 16,
        "SR0": 17,
        "SR1": 18,
        "SR2": 19,
        "SR3": 20,
        "SR4": 21,
        "SR5": 22,
        "SR6": 23,
        "SR7": 24,
        "SR8": 25,
        "SR9": 26,
        "SR10": 27,
        "SR11": 28,
        "SR12": 29,
        "SR13": 30,
        "SR14": 31,
        "SR15": 32,
    }
    if text in mapping:
        return float(mapping[text])
    try:
        return float(text)
    except Exception:
        return None


def _build_row_values(detail: dict) -> List[Any]:
    vals: List[Any] = []
    vals.append(detail.get("breakthrough", ""))
    vals.append(detail.get("skill1_lv", ""))
    vals.append(detail.get("skill2_lv", ""))
    vals.append(detail.get("ulti_skill_lv", ""))
    vals.append(_equip_val(detail.get("head_equip_tier", ""), detail.get("head_equip_lv", "")))
    vals.append(_equip_val(detail.get("arm_equip_tier", ""), detail.get("arm_equip_lv", "")))
    vals.append(_equip_val(detail.get("torso_equip_tier", ""), detail.get("torso_equip_lv", "")))
    vals.append(_equip_val(detail.get("leg_equip_tier", ""), detail.get("leg_equip_lv", "")))

    fav_type = detail.get("favorite_item_type", "")
    fav_lv = detail.get("favorite_item_lv", "")
    if str(fav_type).upper() == "N":
        fav_display = str(fav_type)
    else:
        fav_display = f"{fav_type}{fav_lv}"
    vals.append(fav_display)

    funcs = detail.get("functions", []) or []
    for idx_f in range(3):
        fv = ""
        if idx_f < len(funcs):
            fv = funcs[idx_f].get("function_value", "")
        vals.append(fv)
    return vals


def _equip_val(tier, lv):
    try:
        tier_num = int(tier)
    except Exception:
        tier_num = None
    if tier_num == 0:
        return -1
    if tier_num == 9:
        return "T9"
    return lv


def _apply_updates(
    updates_by_ws: Dict[Any, List[Tuple[int, int, List[Any]]]]
) -> Tuple[bool, List[Dict[str, Any]]]:
    """Commit all training and change cells together; never hide a failed sheet."""
    from gspread.utils import rowcol_to_a1

    if not any(updates_by_ws.values()):
        return True, []
    spreadsheet = next(ws.spreadsheet for ws, items in updates_by_ws.items() if items)
    cells_by_ws = {}
    for ws, items in updates_by_ws.items():
        if ws.spreadsheet.id != spreadsheet.id:
            raise ValueError("不能在同一批次更新不同試算表")
        rows = cells_by_ws.setdefault(ws, {})
        for row_idx, start_col, vals in items:
            if row_idx < 1 or row_idx > ws.row_count or start_col < 1 or start_col + len(vals) - 1 > ws.col_count:
                raise ValueError(f"{ws.title} 的更新超出既有資料列或欄位")
            cells = rows.setdefault(row_idx, {})
            for offset, value in enumerate(vals):
                column = start_col + offset
                if column in cells and not _is_same_value(cells[column], value):
                    raise ValueError(f"{ws.title} 的 {rowcol_to_a1(row_idx, column)} 存在衝突的更新")
                cells[column] = value

    requests_body, notify_items = [], []
    for ws, rows in cells_by_ws.items():
        ranges = []
        for row_idx, cells in sorted(rows.items()):
            columns = sorted(cells)
            index = 0
            while index < len(columns):
                start_col = columns[index]
                values = [cells[start_col]]
                index += 1
                while index < len(columns) and columns[index] == start_col + len(values):
                    values.append(cells[columns[index]])
                    index += 1
                requests_body.append({"updateCells": {
                    "start": {"sheetId": ws.id, "rowIndex": row_idx - 1, "columnIndex": start_col - 1},
                    "rows": [{"values": [_user_entered_cell(value) for value in values]}],
                    "fields": "userEnteredValue",
                }})
                cell_range = f"{rowcol_to_a1(row_idx, start_col)}:{rowcol_to_a1(row_idx, start_col + len(values) - 1)}"
                ranges.append(cell_range)
        if ranges:
            notify_items.append({"sheet_name": ws.title, "ranges": ranges})
    try:
        spreadsheet.batch_update({"requests": requests_body})
    except Exception as exc:
        if not _is_uncertain_write_error(exc):
            raise
        verified = _read_training_values(spreadsheet, list(cells_by_ws))
        for ws, rows in cells_by_ws.items():
            for row, cells in rows.items():
                actual = _source_row(ws, row, verified)
                if any(not _is_same_value(actual[column - 1] if column <= len(actual) else "", value) for column, value in cells.items()):
                    raise ValueError("寫入結果未確認，請重新讀取試算表後再操作") from exc
    return True, notify_items


def _read_training_values(spreadsheet, worksheets):
    from gspread.utils import rowcol_to_a1

    ranges = [f"'{ws.title.replace(chr(39), chr(39) * 2)}'!A1:{rowcol_to_a1(ws.row_count, ws.col_count)}" for ws in worksheets]
    response = spreadsheet.values_batch_get(ranges, params={"valueRenderOption": "UNFORMATTED_VALUE"})
    results = response.get("valueRanges", [])
    if len(results) != len(worksheets):
        raise ValueError("無法完整讀取練度、變化與成員名稱資料，停止整批上傳")
    return {ws: result.get("values", []) for ws, result in zip(worksheets, results)}


def _source_row(worksheet, row, values_by_ws):
    if values_by_ws is None:
        return worksheet.row_values(row)
    values = values_by_ws[worksheet]
    return values[row - 1] if row <= len(values) else []


def _source_column(worksheet, column, values_by_ws):
    if values_by_ws is None:
        return worksheet.col_values(column)
    return [row[column - 1] if len(row) >= column else "" for row in values_by_ws[worksheet]]


def _source_slice(worksheet, row, column, length, values_by_ws):
    values = _source_row(worksheet, row, values_by_ws)
    return values[column - 1:column - 1 + length]


def _unique_rows(values, start_row, title):
    result = {}
    for row, raw in enumerate(values, 1):
        value = str(raw or "").strip()
        if row < start_row or not value:
            continue
        if value in result:
            raise ValueError(f"{title} 包含重複的成員名稱或 Blabla ID：{value}")
        result[value] = row
    return result


def _filter_unchanged_updates(updates_by_ws, values_by_ws):
    changed = {}
    for ws, items in updates_by_ws.items():
        for row, column, values in items:
            original = _source_slice(ws, row, column, len(values), values_by_ws)
            index = 0
            while index < len(values):
                old_value = original[index] if index < len(original) else ""
                if _is_same_value(old_value, values[index]):
                    index += 1
                    continue
                first = index
                run = []
                while index < len(values) and not _is_same_value(original[index] if index < len(original) else "", values[index]):
                    run.append(values[index])
                    index += 1
                changed.setdefault(ws, []).append((row, column + first, run))
    return changed


def _is_same_value(first, second):
    if first in (None, "") or second in (None, ""):
        return first in (None, "") and second in (None, "")
    if isinstance(first, bool) or isinstance(second, bool):
        return type(first) is type(second) and first == second
    if isinstance(first, (int, float)) or isinstance(second, (int, float)):
        try:
            return float(first) == float(second)
        except (TypeError, ValueError):
            return False
    return first == second


def _user_entered_cell(value):
    if value is None or value == "":
        return {}
    if isinstance(value, bool):
        return {"userEnteredValue": {"boolValue": value}}
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise ValueError("練度資料包含無效數值")
        return {"userEnteredValue": {"numberValue": value}}
    return {"userEnteredValue": {"stringValue": str(value)}}


def _is_uncertain_write_error(exc):
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    return getattr(getattr(exc, "response", None), "status_code", 0) >= 500


def _notify_web_app_batch(
    items: List[Dict[str, Any]],
    kind: str = "training",
    spreadsheet_id: str | None = None,
) -> None:
    target_spreadsheet_id = str(spreadsheet_id or "").strip()
    if not WEB_APP_URL or not items or not target_spreadsheet_id:
        return
    try:
        payload_dir = CHAR_DETAILS_DIR.parent / "WebAppNotify"
        payload_dir.mkdir(parents=True, exist_ok=True)
        payload_path = payload_dir / f"notify_{uuid.uuid4().hex}.json"
        payload = {
            "url": WEB_APP_URL,
            "payload": {"spreadsheet_id": target_spreadsheet_id, "items": items},
            "kind": kind,
        }
        payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        worker_path = Path(__file__).resolve().parents[2] / "webapp_notify_worker.py"
        creationflags = 0
        for attr in ("CREATE_NO_WINDOW", "DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP"):
            creationflags |= getattr(subprocess, attr, 0)
        subprocess.Popen(
            [sys.executable, str(worker_path), str(payload_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
        )
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
