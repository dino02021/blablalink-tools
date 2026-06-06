# -*- coding: utf-8 -*-
"""妮姬資料上傳流程與共用試算表 helper。"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional

from ...jobs.nikke_upload import run_nikke_upload
from ...constants import CHAR_DETAILS_DIR, CHAR_DETAILS_TYPE, WEB_APP_URL
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
    main_sheets = []
    for ws in worksheets:
        title = ws.title
        if title.startswith("角色練度_變化"):
            continue
        if title.startswith("角色練度"):
            main_sheets.append(ws)
    return main_sheets


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


def _load_change_headers(change_sheet) -> Tuple[Dict[str, int], Dict[str, List[int]]]:
    if change_sheet is None:
        return {}, {}
    try:
        nick_col = change_sheet.col_values(1)
        nick_to_row = {v.strip(): i for i, v in enumerate(nick_col, start=1) if v.strip()}
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        nick_to_row = {}
    try:
        hdrs = change_sheet.row_values(2)
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        hdrs = []
    pos_map: Dict[str, List[int]] = {}
    for idx, h in enumerate(hdrs, start=1):
        name = h.strip()
        if not name:
            continue
        pos_map.setdefault(name, []).append(idx)
    return nick_to_row, pos_map


def _load_sheet_headers(
    main_sheets,
    member_sheet: Any | None = None,
) -> Tuple[Dict[Any, Dict[str, int]], Dict[str, str], Dict[Any, Dict[str, int]], Dict[Any, Dict[str, List[int]]]]:
    member_id_to_nick: Dict[str, str] = {}
    if member_sheet is not None:
        try:
            member_names = member_sheet.col_values(1)
            member_ids = member_sheet.col_values(3)
            for row_idx, member_id in enumerate(member_ids, start=1):
                clean_id = str(member_id or "").strip()
                if not clean_id:
                    continue
                name = str(member_names[row_idx - 1] if row_idx - 1 < len(member_names) else "").strip()
                if name:
                    member_id_to_nick[clean_id] = name
        except Exception as exc:  # noqa: BLE001
            log_exc(exc)

    nick_to_row_by_ws: Dict[Any, Dict[str, int]] = {}
    member_id_to_row_by_ws: Dict[Any, Dict[str, int]] = {}
    header_pos: Dict[Any, Dict[str, List[int]]] = {}
    for ws in main_sheets:
        try:
            nick_col = ws.col_values(1)
            nick_to_row_by_ws[ws] = {v.strip(): i for i, v in enumerate(nick_col, start=1) if v.strip()}
        except Exception as exc:  # noqa: BLE001
            log_exc(exc)
            nick_to_row_by_ws[ws] = {}

        if member_sheet is None:
            try:
                member_id_col = ws.col_values(3)
                member_id_to_row_by_ws[ws] = {
                    v.strip(): i for i, v in enumerate(member_id_col, start=1) if v.strip()
                }
            except Exception as exc:  # noqa: BLE001
                log_exc(exc)
                member_id_to_row_by_ws[ws] = {}

        try:
            hdrs = ws.row_values(1)
        except Exception as exc:  # noqa: BLE001
            log_exc(exc)
            hdrs = []
        pos_map: Dict[str, List[int]] = {}
        for idx, h in enumerate(hdrs, start=1):
            name = h.strip()
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
    for detail in details:
        name = detail.get("name")
        if not name:
            continue
        for ws in main_sheets:
            start_cols = header_pos.get(ws, {}).get(name, [])
            if not start_cols:
                continue
            nick_to_row = nick_to_row_by_ws.get(ws, {})
            member_id_to_row = member_id_to_row_by_ws.get(ws, {})
            row_idx = None
            if member_id:
                member_nick = member_id_to_nick.get(member_id, "")
                if member_nick:
                    row_idx = nick_to_row.get(member_nick)
                if not row_idx:
                    row_idx = member_id_to_row.get(member_id)
            if not row_idx:
                row_idx = nick_to_row.get(nickname)
            if not row_idx:
                continue
            vals = _build_row_values(detail)
            for start_col in start_cols:
                updates_by_ws.setdefault(ws, []).append((row_idx, start_col, vals))
                if change_sheet:
                    pending_changes.append((ws, row_idx, start_col, vals, nickname, name))

    return updates_by_ws, pending_changes


def _populate_change_updates(
    pending: List[Tuple[Any, int, int, List[Any], str, str]],
    combined_changes: Dict[Any, List[Tuple[int, int, List[Any]]]],
    change_sheet: Any | None,
    change_nick_to_row: Dict[str, int],
    change_header_pos: Dict[str, List[int]],
) -> None:
    if not change_sheet or not pending:
        return
    try:
        from gspread.utils import rowcol_to_a1  # type: ignore
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return

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
        try:
            values = _batch_get_ranges(ws, ranges, 80)
        except Exception as exc:  # noqa: BLE001
            log_msg(f"nikke_upload change read failed ws={ws.title} count={len(ranges)}")
            log_exc(exc)
            continue
        change_values: List[List[List[str]]] = []
        if change_ranges:
            try:
                change_values = _batch_get_ranges(change_sheet, change_ranges, 80)
            except Exception as exc:  # noqa: BLE001
                log_msg(f"nikke_upload change read failed ws={change_sheet.title} count={len(change_ranges)}")
                log_exc(exc)
                change_values = []
        for (row_idx, start_col, vals, nickname, name), old_block in zip(items, values):
            old_vals = old_block[0] if old_block else []
            diffs = _build_diff_values_from_slice(old_vals, vals)
            if not any(diffs):
                continue
            change_row = change_nick_to_row.get(nickname)
            change_cols = change_header_pos.get(name, [])
            if not change_row or not change_cols:
                continue
            for change_col in change_cols:
                existing_vals = _get_change_existing(change_values, change_ranges, change_row, change_col, len(vals))
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
        out.extend(sheet.batch_get(chunk))
    return out


def _merge_change_values(existing: List[str], diffs: List[Any]) -> List[Any]:
    out: List[Any] = []
    for idx, diff in enumerate(diffs):
        if diff == "":
            out.append("")
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
    vals.append(_equip_val(detail.get("head_equip_tier", 0), detail.get("head_equip_lv", "")))
    vals.append(_equip_val(detail.get("arm_equip_tier", 0), detail.get("arm_equip_lv", "")))
    vals.append(_equip_val(detail.get("torso_equip_tier", 0), detail.get("torso_equip_lv", "")))
    vals.append(_equip_val(detail.get("leg_equip_tier", 0), detail.get("leg_equip_lv", "")))

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
    if tier == 9:
        return "T9"
    return lv


def _apply_updates(
    updates_by_ws: Dict[Any, List[Tuple[int, int, List[Any]]]]
) -> Tuple[bool, List[Dict[str, Any]]]:
    try:
        from gspread.utils import rowcol_to_a1  # type: ignore
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return False, []

    any_written = False
    notify_items: List[Dict[str, Any]] = []
    for ws, items in updates_by_ws.items():
        if not items:
            continue
        payload = []
        for row_idx, start_col, vals in items:
            end_col = start_col + len(vals) - 1
            rng = f"{rowcol_to_a1(row_idx, start_col)}:{rowcol_to_a1(row_idx, end_col)}"
            payload.append({"range": rng, "values": [vals]})
        try:
            if payload:
                ws.batch_update(payload, value_input_option="RAW")
                any_written = True
                notify_items.append(
                    {"sheet_name": ws.title, "ranges": [item["range"] for item in payload]}
                )
        except Exception as exc:  # noqa: BLE001
            ranges = [item.get("range") for item in payload]
            sample = [r for r in ranges if r][:5]
            log_msg(
                "nikke_upload batch_update failed ws="
                f"{ws.title} count={len(payload)} sample_ranges={sample}"
            )
            log_exc(exc)
    return any_written, notify_items


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
