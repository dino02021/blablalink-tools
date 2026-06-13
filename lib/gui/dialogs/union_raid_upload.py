# -*- coding: utf-8 -*-
"""Union raid upload dialog and sheet upload workflow."""

from __future__ import annotations

import json
import os
import re
import threading
import asyncio
import base64
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import tkinter as tk
from tkinter import ttk

import gspread

UNION_RAID_TOP_ROW_HEIGHT_ONE = 30
UNION_RAID_TOP_ROW_HEIGHT_TWO = 40
UNION_RAID_TOP_ROW_HEIGHT_THREE_PLUS = 50
UNION_RAID_IMAGE_ROW_HEIGHT_ONE = 50
UNION_RAID_IMAGE_ROW_HEIGHT_TWO = 90
UNION_RAID_IMAGE_ROW_HEIGHT_THREE_PLUS = 130

from ..layout import (
    create_dialog,
    close_dialog,
    confirm_dialog,
    create_frame,
    create_btn_frame,
    create_btn_between,
    create_btn_last,
    create_entry_frame,
    create_entry_label,
    create_entry,
    create_msg_label,
    create_listbox,
)
from ...jobs.union_raid_upload import run_union_raid_upload, resolve_union_sheet_target
from ...constants import (
    CHAR_DETAILS_TYPE,
    NIKKES_INFO_FILE,
    NIKKES_INFO_URL,
    UNION_RAID_TEMP_FILE,
    UNION_RAID_TYPE,
    UNION_WEB_APP_URL,
)
from ...data_store import load_sheets
from ...github_data_sync import read_cloud_data_json
from ...utils import col_letter
from ...utils import load_session, run_with_session_context
from ...google_sheets.auth import get_gspread_client
from ...log import log_exc, log_msg
from ...api import fetch_guild_members


@dataclass
class UnionUploadState:
    data_path: Optional[Path] = None
    sheet_id: Optional[str] = None
    sheet_name: Optional[str] = None
    month_text: Optional[str] = None


@dataclass
class UnionUploadUi:
    parent: tk.Misc
    dlg: tk.Toplevel
    data_var: tk.StringVar
    sheet_var: tk.StringVar
    month_var: tk.StringVar
    status_var: tk.StringVar
    status_label: ttk.Label
    data_btn: ttk.Button
    month_btn: ttk.Button
    upload_btn: ttk.Button


def open_upload_union_dialog(app) -> None:
    """Open union raid upload dialog."""
    dlg = create_dialog(app.root, "上傳聯盟突襲資料", 480, 200)

    frame = create_frame(dlg)
    entry_frame = create_entry_frame(frame, row=0, column=0)

    data_var = tk.StringVar(value="")
    sheet_var = tk.StringVar(value="")
    month_var = tk.StringVar(value="")
    status_var = tk.StringVar(value="")

    create_entry_label(entry_frame, "資料", row=0, column=0)
    data_entry = create_entry(entry_frame, data_var, row=0, column=1)
    data_entry.configure(state="readonly")

    create_entry_label(entry_frame, "試算表", row=1, column=0)
    sheet_entry = create_entry(entry_frame, sheet_var, row=1, column=1)
    sheet_entry.configure(state="readonly")

    create_entry_label(entry_frame, "月份", row=2, column=0)
    month_entry = create_entry(entry_frame, month_var, row=2, column=1)
    month_entry.configure(state="readonly")

    btns = create_btn_frame(frame, row=1, column=0)
    btns.columnconfigure(0, weight=0)
    btns.columnconfigure(1, weight=0)
    btns.columnconfigure(2, weight=1)

    data_btn = create_btn_between(btns, "選擇資料", lambda: None, row=0, column=0, sticky="w")
    month_btn = create_btn_between(btns, "選擇月份", lambda: None, row=0, column=1, sticky="w")
    upload_btn = create_btn_last(btns, "上傳", lambda: None, row=0, column=2, sticky="we")

    status_label = create_msg_label(frame, status_var, row=2, column=0, sticky="e")
    status_label.configure(foreground="green", anchor="e", justify="right")

    ui = UnionUploadUi(
        parent=app.root,
        dlg=dlg,
        data_var=data_var,
        sheet_var=sheet_var,
        month_var=month_var,
        status_var=status_var,
        status_label=status_label,
        data_btn=data_btn,
        month_btn=month_btn,
        upload_btn=upload_btn,
    )
    state = UnionUploadState()

    data_btn.config(command=lambda: _open_data_dialog(ui, state))
    month_btn.config(command=lambda: _open_month_dialog(ui, state))
    upload_btn.config(command=lambda: _start_upload(app, ui, state))

    dlg.protocol("WM_DELETE_WINDOW", lambda: close_dialog(dlg))
    _refresh_upload_state(ui, state)


# ---------------- internal helpers ----------------

def _set_upload_status(ui: UnionUploadUi, text: str, color: str) -> None:
    ui.status_var.set(text)
    ui.status_label.configure(foreground=color)


def _refresh_upload_state(ui: UnionUploadUi, state: UnionUploadState) -> None:
    if not state.data_path or not state.sheet_id:
        ui.month_btn.config(state="disabled")
    else:
        ui.month_btn.config(state="normal")

    is_ready = bool(state.data_path and state.sheet_id and state.month_text)
    ui.upload_btn.config(state="normal" if is_ready else "disabled")


def _disable_upload_controls(ui: UnionUploadUi) -> None:
    ui.data_btn.config(state="disabled")
    ui.month_btn.config(state="disabled")
    ui.upload_btn.config(state="disabled")


def _enable_upload_controls(ui: UnionUploadUi, state: UnionUploadState) -> None:
    ui.data_btn.config(state="normal")
    _refresh_upload_state(ui, state)


def _open_data_dialog(ui: UnionUploadUi, state: UnionUploadState) -> None:
    dlg = create_dialog(ui.dlg, "選擇資料", 400, 320)
    frame = create_frame(dlg)
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(0, weight=1)

    listbox = create_listbox(frame, row=0, column=0)
    files = _list_union_files()
    for f in files:
        listbox.insert("end", f" {f.name}")

    btns = create_btn_frame(frame, row=1, column=0)
    btns.columnconfigure(0, weight=1)
    btns.columnconfigure(1, weight=0)
    btns.columnconfigure(2, weight=0)
    btns.columnconfigure(3, weight=0)

    msg_var = tk.StringVar(value="")
    msg_label = create_msg_label(frame, msg_var, row=2, column=0, sticky="w")
    msg_label.configure(foreground="red", anchor="w", justify="left")

    create_btn_between(
        btns,
        "套用選擇",
        lambda: _apply_data_choice(dlg, listbox, files, ui, state, msg_var, msg_label),
        row=0,
        column=0,
        sticky="we",
    )
    create_btn_between(
        btns,
        "開啟資料夾",
        _open_union_folder,
        row=0,
        column=1,
        sticky="e",
    )
    create_btn_between(
        btns,
        "刪除資料",
        lambda: _delete_selected_data(listbox, files, msg_var, msg_label),
        row=0,
        column=2,
        sticky="e",
    )
    create_btn_last(
        btns,
        "刪除全部資料",
        lambda: _delete_all_data(listbox, files),
        row=0,
        column=3,
        sticky="e",
    )

    listbox.bind(
        "<Double-Button-1>",
        lambda e=None: _apply_data_choice(dlg, listbox, files, ui, state, msg_var, msg_label),
    )
    dlg.protocol("WM_DELETE_WINDOW", lambda: close_dialog(dlg))
    ui.parent.wait_window(dlg)


def _apply_data_choice(
    dlg: tk.Toplevel,
    listbox: tk.Listbox,
    files: List[Path],
    ui: UnionUploadUi,
    state: UnionUploadState,
    msg_var: tk.StringVar,
    msg_label: ttk.Label,
) -> None:
    idx = _get_selected_index(listbox)
    if idx is None:
        msg_var.set("請選擇資料")
        msg_label.configure(foreground="red")
        return
    state.data_path = files[idx]
    ui.data_var.set(files[idx].name)
    state.month_text = None
    ui.month_var.set("")
    _sync_sheet_from_data(ui, state)
    if state.sheet_id:
        _set_upload_status(ui, "", "green")
    _refresh_upload_state(ui, state)
    close_dialog(dlg)


def _sync_sheet_from_data(ui: UnionUploadUi, state: UnionUploadState) -> None:
    state.sheet_id = None
    state.sheet_name = None
    ui.sheet_var.set("")
    if not state.data_path:
        return
    try:
        union_id, sheet_id, sheet_name = resolve_union_sheet_target(data_path=state.data_path)
    except Exception as exc:  # noqa: BLE001
        _set_upload_status(ui, str(exc), "red")
        return

    state.sheet_id = sheet_id or None
    state.sheet_name = sheet_name or None
    display_name = state.sheet_name or state.sheet_id or ""
    if union_id:
        display_name = f"{display_name} ({union_id})" if display_name else union_id
    ui.sheet_var.set(display_name)


def _delete_selected_data(
    listbox: tk.Listbox, files: List[Path], msg_var: tk.StringVar, msg_label: ttk.Label
) -> None:
    idx = _get_selected_index(listbox)
    if idx is None:
        msg_var.set("請選擇要刪除的資料")
        msg_label.configure(foreground="red")
        return
    if not confirm_dialog(listbox.winfo_toplevel(), "確定要刪除選取的資料嗎？"):
        return
    try:
        files[idx].unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
    listbox.delete(0, "end")
    for f in _list_union_files():
        listbox.insert("end", f" {f.name}")


def _delete_all_data(listbox: tk.Listbox, files: List[Path]) -> None:
    if not files:
        return
    if not confirm_dialog(listbox.winfo_toplevel(), "確定要刪除全部資料嗎？"):
        return
    for f in files:
        try:
            f.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001
            log_exc(exc)
    listbox.delete(0, "end")


def _open_union_folder() -> None:
    try:
        UNION_RAID_TEMP_FILE.parent.mkdir(parents=True, exist_ok=True)
        os.startfile(str(UNION_RAID_TEMP_FILE.parent))
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)


def _open_month_dialog(ui: UnionUploadUi, state: UnionUploadState) -> None:
    if not state.sheet_id:
        _set_upload_status(ui, "找不到對應試算表", "red")
        return
    ui.month_btn.config(state="disabled")
    _set_upload_status(ui, "讀取試算表中...", "green")
    threading.Thread(target=_fetch_months_thread, args=(ui, state), daemon=True).start()


def _open_month_dialog_ui(ui: UnionUploadUi, state: UnionUploadState, months: List[str]) -> None:
    ui.month_btn.config(state="normal")
    _set_upload_status(ui, "", "green")
    if not months:
        _set_upload_status(ui, "找不到月份", "red")
        return

    dlg = create_dialog(ui.dlg, "選擇月份", 320, 300)
    frame = create_frame(dlg)
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(0, weight=1)

    listbox = create_listbox(frame, row=0, column=0)
    for m in months:
        listbox.insert("end", f" {m}")

    btns = create_btn_frame(frame, row=1, column=0)
    btns.columnconfigure(0, weight=1)

    msg_var = tk.StringVar(value="")
    msg_label = create_msg_label(frame, msg_var, row=2, column=0, sticky="w")
    msg_label.configure(foreground="red", anchor="w", justify="left")

    create_btn_last(
        btns,
        "套用選擇",
        lambda: _apply_month_choice(dlg, listbox, ui, state, msg_var, msg_label),
        row=0,
        column=0,
        sticky="we",
    )

    listbox.bind(
        "<Double-Button-1>",
        lambda e=None: _apply_month_choice(dlg, listbox, ui, state, msg_var, msg_label),
    )
    dlg.protocol("WM_DELETE_WINDOW", lambda: close_dialog(dlg))
    ui.parent.wait_window(dlg)


def _fetch_months_thread(ui: UnionUploadUi, state: UnionUploadState) -> None:
    titles = _get_sheet_titles(state.sheet_id or "")
    months = _extract_months_from_titles(titles or [])
    ui.parent.after(0, lambda: _open_month_dialog_ui(ui, state, months))


def _apply_month_choice(
    dlg: tk.Toplevel,
    listbox: tk.Listbox,
    ui: UnionUploadUi,
    state: UnionUploadState,
    msg_var: tk.StringVar,
    msg_label: ttk.Label,
) -> None:
    idx = _get_selected_index(listbox)
    if idx is None:
        msg_var.set("請選擇月份")
        msg_label.configure(foreground="red")
        return
    month = listbox.get(idx).strip()
    state.month_text = month
    ui.month_var.set(month)
    _set_upload_status(ui, "", "green")
    _refresh_upload_state(ui, state)
    close_dialog(dlg)


def _start_upload(app, ui: UnionUploadUi, state: UnionUploadState) -> None:
    if not state.data_path or not state.data_path.exists():
        _set_upload_status(ui, "資料檔不存在", "red")
        return
    if not state.sheet_id:
        _set_upload_status(ui, "找不到對應試算表", "red")
        return
    if not state.month_text:
        _set_upload_status(ui, "請選擇月份", "red")
        return

    _disable_upload_controls(ui)
    _set_upload_status(ui, "上傳中...", "green")
    threading.Thread(target=_upload_worker, args=(app, ui, state), daemon=True).start()


def _run_coro_sync(coro_factory):
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(coro_factory())
        loop.run_until_complete(loop.shutdown_asyncgens())
        return result
    finally:
        asyncio.set_event_loop(None)
        loop.close()


def _upload_worker(app, ui: UnionUploadUi, state: UnionUploadState) -> None:
    status = "fail"
    msg = "上傳失敗"
    try:
        result = run_union_raid_upload(
            account=str(getattr(app, "_last_slug", "") or "").strip(),
            month=state.month_text or "",
            data_path=state.data_path,
            sheet_id=state.sheet_id or "",
        )
        msg = result.message
        if not result.ok:
            status = "fail"
        elif result.stats.get("partial"):
            status = "partial"
        else:
            status = "success"
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        if str(exc):
            msg = str(exc)
    ui.parent.after(0, lambda: _finish_upload(app, ui, state, status, msg))


def _finish_upload(app, ui: UnionUploadUi, state: UnionUploadState, status: str, msg: str) -> None:
    _enable_upload_controls(ui, state)
    if status == "success":
        _set_upload_status(ui, "", "green")
        try:
            app.union_status_var.set("上傳成功")
            app.union_status_label.configure(foreground="green")
        except Exception as exc:  # noqa: BLE001
            log_exc(exc)
        close_dialog(ui.dlg)
        return
    if status == "partial":
        _set_upload_status(ui, msg, "red")
        try:
            app.union_status_var.set(msg)
            app.union_status_label.configure(foreground="red")
        except Exception as exc:  # noqa: BLE001
            log_exc(exc)
        return
    _set_upload_status(ui, msg or "上傳失敗", "red")
    try:
        app.union_status_var.set("上傳失敗")
        app.union_status_label.configure(foreground="red")
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)


def _upload_to_sheets(
    sheet_id: str,
    month: str,
    items: List[Dict[str, Any]],
    guild_members: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[bool, bool, str]:
    if not sheet_id or not month:
        log_msg(f"union_upload invalid_args sheet_id={sheet_id} month={month}")
        return False, False, "sheet_id ??month ?⊥?"
    try:
        cli = get_gspread_client()
        sh = cli.open_by_key(sheet_id)
        ws_name = f"{month} 出刀總表"
        ws = sh.worksheet(ws_name)
        log_msg(f"union_upload open_sheet ws={ws.title}")
    except gspread.exceptions.WorksheetNotFound as exc:
        log_exc(exc)
        log_msg(f"union_upload fail worksheet_not_found month={month}")
        raise ValueError("找不到目標工作表") from exc
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        log_msg(f"union_upload fail open_sheet exception={exc}")
        raise ValueError("找不到目標工作表") from exc

    id_to_name, id_to_image_url = _load_nikke_upload_maps()
    step_cols = _extract_step_columns(ws)
    if not step_cols:
        log_msg(f"union_upload fail step_columns_empty ws={ws.title}")
        return False, False, "找不到一~五王欄位"

    rows = ws.col_values(1)
    member_rows = _extract_member_rows(sh, ws, rows)
    if not member_rows:
        log_msg(f"union_upload member_rows_empty ws={ws.title}, fallback_to_generated_slots")

    max_members = 32
    payload_by_name: Dict[str, Dict[str, Any]] = {}
    upload_order_index: Dict[str, int] = {}
    damage_members: List[Dict[str, Any]] = []
    for input_index, item in enumerate(items):
        username = str(item.get("username", "")).strip()
        if not username:
            continue
        if username not in payload_by_name:
            payload_by_name[username] = item
            upload_order_index[username] = input_index
        total_damage = _calc_total_damage(item)
        if total_damage <= 0:
            continue
        damage_members.append({"index": input_index, "username": username, "total_damage": total_damage})
    damage_members.sort(key=lambda x: (-x["total_damage"], x["index"]))

    guild_by_name: Dict[str, Dict[str, Any]] = {}
    guild_sorted: List[Dict[str, Any]] = []
    for idx, gm in enumerate(guild_members or []):
        if not isinstance(gm, dict):
            continue
        nickname = str(gm.get("nickname", "")).strip()
        if not nickname:
            continue
        entry = {
            "index": idx,
            "nickname": nickname,
            "synchro_level": _to_int(gm.get("synchro_level")) or 0,
        }
        guild_by_name[nickname] = entry
        guild_sorted.append(entry)
    guild_sorted.sort(key=lambda x: (-x["synchro_level"], x["index"]))
    guild_level_map = {x["nickname"]: x["synchro_level"] for x in guild_sorted}

    ordered_names: List[str] = []
    seen_names: set[str] = set()
    for item in damage_members:
        name = item["username"]
        if name in seen_names:
            continue
        seen_names.add(name)
        ordered_names.append(name)

    # 上傳資料中未進入傷害榜的成員，依同步等級降序（同值依原上傳順序）
    non_damage_upload_names = [name for name in payload_by_name.keys() if name not in seen_names]
    non_damage_upload_names.sort(
        key=lambda name: (
            -int(guild_level_map.get(name, 0) or 0),
            int(upload_order_index.get(name, 10**9)),
        )
    )
    for name in non_damage_upload_names:
        if name in seen_names:
            continue
        seen_names.add(name)
        ordered_names.append(name)

    # 只有上傳資料不足 32 名時，才從公會名單補齊
    if len(ordered_names) < max_members:
        for gm in guild_sorted:
            name = gm["nickname"]
            if name in seen_names:
                continue
            seen_names.add(name)
            ordered_names.append(name)
            if len(ordered_names) >= max_members:
                break

    # 上傳資料已達 32（或超過）時，不再額外補人；最終名單最多 32
    ordered_names = ordered_names[:max_members]

    ordered_members: List[Dict[str, Any]] = []
    for idx, name in enumerate(ordered_names):
        payload_item = payload_by_name.get(name)
        if payload_item is None:
            payload_item = {"username": name, "lv": guild_by_name.get(name, {}).get("synchro_level", ""), "data": []}
        else:
            guild_lv = guild_by_name.get(name, {}).get("synchro_level")
            if guild_lv is not None and guild_lv != "":
                payload_item = dict(payload_item)
                payload_item["lv"] = guild_lv
        ordered_members.append({"index": idx, "username": name, "item": payload_item})

    available_slots = _expand_member_rows(member_rows, len(ordered_members))
    log_msg(
        f"union_upload mapping payload={len(items)} upload_unique={len(payload_by_name)} ordered={len(ordered_members)} slots={len(available_slots)} max={max_members}"
    )
    if available_slots:
        needed_rows = max(available_slots) + 1
        if int(ws.row_count) < needed_rows:
            log_msg(f"union_upload resize rows old={ws.row_count} new={needed_rows}")
            ws.resize(rows=needed_rows)
        ws.batch_update(
            [{"range": f"A2:L{needed_rows}", "values": [[""] * 12 for _ in range(needed_rows - 1)]}],
            value_input_option="RAW",
        )

    ordered_with_rows: List[Tuple[int, str, Dict[str, Any]]] = []
    for idx, entry in enumerate(ordered_members):
        if idx >= len(available_slots):
            break
        row_idx = available_slots[idx]
        ordered_with_rows.append((row_idx, entry["username"], entry["item"]))

    a_updates: List[Dict[str, Any]] = []
    for row_idx, username, _ in ordered_with_rows:
        a_updates.append(_build_row_update(row_idx, 1, [username]))

    total_payload: List[Dict[str, Any]] = []
    image_payload: List[Dict[str, Any]] = []
    damage_cells: List[Dict[str, Any]] = []
    top_row_heights: Dict[int, int] = {}
    image_row_heights: Dict[int, int] = {}

    for row_idx, username, item in ordered_with_rows:
        if not username:
            continue
        lv_val = _coerce_number(item.get("lv", ""))
        if lv_val != "":
            total_payload.append(_build_row_update(row_idx, 2, [lv_val]))

        grouped = _group_by_step(item.get("data", []))
        max_same_step = 1
        for entries in grouped.values():
            if len(entries) > max_same_step:
                max_same_step = len(entries)
        top_height, image_height = _get_union_raid_row_heights(max_same_step)
        top_row_heights[row_idx] = top_height
        image_row_heights[row_idx + 1] = image_height

        for step, entries in grouped.items():
            cols = step_cols.get(step)
            if not cols:
                continue
            damage_lines: List[str] = []
            level_lines: List[str] = []
            final_line_indexes: List[int] = []
            for entry in entries:
                damage_text = _format_damage_text(entry.get("damage"))
                if damage_text != "":
                    line_idx = len(damage_lines)
                    damage_lines.append(damage_text)
                    if entry.get("is_final_hit"):
                        final_line_indexes.append(line_idx)
                level_lines.append(_to_roman_text(entry.get("level")))

            if damage_lines:
                damage_cells.append(
                    {
                        "row": row_idx,
                        "col": cols["damage_col"],
                        "text": "\n".join(damage_lines),
                        "final_lines": final_line_indexes,
                    }
                )
                image_formula = _build_union_raid_team_image_formula(entries, id_to_image_url)
                if image_formula:
                    image_payload.append(_build_row_update(row_idx + 1, cols["damage_col"], [image_formula]))
            if level_lines:
                total_payload.append(
                    _build_row_update(row_idx, cols["level_col"], ["\n".join(level_lines)])
                )

    if a_updates:
        _apply_batch_updates(ws, a_updates)
    _apply_row_heights(sh, ws, top_row_heights, image_row_heights)
    _apply_batch_updates(ws, total_payload)
    _apply_formula_updates(ws, image_payload)
    _apply_damage_cells_with_color(sh, ws, damage_cells)
    log_msg(
        "union_upload apply_updates "
        f"names={len(a_updates)} values={len(total_payload)} images={len(image_payload)} damage_cells={len(damage_cells)}"
    )
    web_members = []
    for _, _, item in ordered_with_rows:
        web_members.append(_build_web_member(item, id_to_name))

    log_msg(
        "union_upload webapp_payload "
        f"nikke_map={len(id_to_name)} members={len(web_members)} "
        f"sample={json.dumps(_summarize_web_members(web_members), ensure_ascii=False)}"
    )
    notify_sheet_id, notify_union_id = _resolve_raid_plan_sheet_for_union_upload(sheet_id)
    if notify_sheet_id != sheet_id:
        log_msg(
            "union_upload webapp_target "
            f"union_id={notify_union_id or '-'} damage_sheet_id={sheet_id} raid_plan_sheet_id={notify_sheet_id}"
        )
    web_ok, web_msg = _notify_union_web_app(
        month,
        notify_sheet_id,
        web_members,
        damage_sheet_id=sheet_id,
        union_id=notify_union_id,
    )
    log_msg(f"union_upload webapp members={len(web_members)} ok={web_ok} msg={web_msg}")
    return True, web_ok, web_msg


def _calc_total_damage(item: Dict[str, Any]) -> int:
    data_list = item.get("data", [])
    if not isinstance(data_list, list):
        return 0
    total = 0
    for entry in data_list[:3]:
        if not isinstance(entry, dict):
            continue
        val = _to_int(entry.get("damage"))
        if val is None:
            continue
        total += val
    return total


def _extract_step_columns(ws) -> Dict[int, Dict[str, int]]:
    headers = ws.row_values(1)
    label_to_step = {
        "一王": 1,
        "二王": 2,
        "三王": 3,
        "四王": 4,
        "五王": 5,
    }
    out: Dict[int, Dict[str, int]] = {}
    for idx, title in enumerate(headers, start=1):
        text = str(title).strip()
        if not text:
            continue
        for label, step in label_to_step.items():
            if label not in text:
                continue
            if step in out:
                continue
            out[step] = {"damage_col": idx, "level_col": idx + 1}
    return out


def _extract_member_rows(sh, ws, rows: List[str]) -> List[int]:
    """Return top rows of merged cells in column A (member blocks)."""
    result: List[int] = []
    try:
        meta = sh.fetch_sheet_metadata()
        sheets = meta.get("sheets", []) if isinstance(meta, dict) else []
        target = None
        for item in sheets:
            props = item.get("properties", {}) if isinstance(item, dict) else {}
            if props.get("sheetId") == ws.id:
                target = item
                break
        merges = target.get("merges", []) if isinstance(target, dict) else []
        for m in merges:
            if not isinstance(m, dict):
                continue
            start_col = int(m.get("startColumnIndex", -1) or -1)
            end_col = int(m.get("endColumnIndex", -1) or -1)
            start_row = int(m.get("startRowIndex", -1) or -1)
            if start_col != 0 or end_col != 1 or start_row < 1:
                continue
            result.append(start_row + 1)
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)

    result = sorted(set(result))
    if result:
        return result

    # Fallback: assume member blocks occupy every other row from row 2.
    fallback_last = len(rows)
    if fallback_last < 2:
        return [2]
    return list(range(2, fallback_last + 1, 2))


def _expand_member_rows(member_rows: List[int], required_count: int) -> List[int]:
    rows = sorted(set(member_rows))
    if required_count <= 0:
        return rows
    if not rows:
        return [2 + i * 2 for i in range(required_count)]
    next_row = rows[-1] + 2
    while len(rows) < required_count:
        rows.append(next_row)
        next_row += 2
    return rows


def _group_by_step(data_list: Any) -> Dict[int, List[Dict[str, Any]]]:
    out: Dict[int, List[Dict[str, Any]]] = {}
    if not isinstance(data_list, list):
        return out
    for entry in data_list:
        if not isinstance(entry, dict):
            continue
        step_val = _normalize_raid_boss_step(entry.get("step"))
        if step_val is None:
            continue
        out.setdefault(step_val, []).append(entry)
    return out


def _normalize_raid_boss_step(value: Any) -> Optional[int]:
    step = _to_int(value)
    if step is None:
        return None
    if 1 <= step <= 5:
        return step
    if step == 6:
        return 5
    return None


def _get_union_raid_row_heights(max_same_step: int) -> Tuple[int, int]:
    if max_same_step >= 3:
        return UNION_RAID_TOP_ROW_HEIGHT_THREE_PLUS, UNION_RAID_IMAGE_ROW_HEIGHT_THREE_PLUS
    if max_same_step == 2:
        return UNION_RAID_TOP_ROW_HEIGHT_TWO, UNION_RAID_IMAGE_ROW_HEIGHT_TWO
    return UNION_RAID_TOP_ROW_HEIGHT_ONE, UNION_RAID_IMAGE_ROW_HEIGHT_ONE


def _apply_row_heights(sh, ws, top_heights: Dict[int, int], image_heights: Dict[int, int]) -> None:
    requests: List[Dict[str, Any]] = []
    for row_idx, px in sorted(top_heights.items()):
        requests.append(
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": ws.id,
                        "dimension": "ROWS",
                        "startIndex": row_idx - 1,
                        "endIndex": row_idx,
                    },
                    "properties": {"pixelSize": px},
                    "fields": "pixelSize",
                }
            }
        )
    for row_idx, px in sorted(image_heights.items()):
        requests.append(
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": ws.id,
                        "dimension": "ROWS",
                        "startIndex": row_idx - 1,
                        "endIndex": row_idx,
                    },
                    "properties": {"pixelSize": px},
                    "fields": "pixelSize",
                }
            }
        )
    if not requests:
        return
    try:
        sh.batch_update({"requests": requests})
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)


def _apply_damage_cells_with_color(sh, ws, damage_cells: List[Dict[str, Any]]) -> None:
    if not damage_cells:
        return
    requests: List[Dict[str, Any]] = []
    for cell in damage_cells:
        row_idx = int(cell["row"])
        col_idx = int(cell["col"])
        text = str(cell.get("text", ""))
        final_lines = cell.get("final_lines", [])
        runs = _build_damage_text_runs(text, final_lines)
        requests.append(
            {
                "updateCells": {
                    "start": {
                        "sheetId": ws.id,
                        "rowIndex": row_idx - 1,
                        "columnIndex": col_idx - 1,
                    },
                    "rows": [
                        {
                            "values": [
                                {
                                    "userEnteredValue": {"stringValue": text},
                                    "textFormatRuns": runs,
                                }
                            ]
                        }
                    ],
                    "fields": "userEnteredValue,textFormatRuns",
                }
            }
        )
    try:
        sh.batch_update({"requests": requests})
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)


def _build_damage_text_runs(text: str, final_line_indexes: List[int]) -> List[Dict[str, Any]]:
    lines = text.split("\n")
    final_set = set(final_line_indexes or [])
    runs: List[Dict[str, Any]] = []
    start = 0
    red = {"red": 204 / 255.0, "green": 65 / 255.0, "blue": 37 / 255.0}
    black = {"red": 0.0, "green": 0.0, "blue": 0.0}
    for idx, line in enumerate(lines):
        color = red if idx in final_set else black
        runs.append({"startIndex": start, "format": {"foregroundColor": color}})
        start += len(line) + 1
    return runs


NIKKE_STATIC_RESOURCE_BASE_URL = "https://sg-tools-cdn.blablalink.com"
NIKKE_AVATAR_MAP_PATH = "character/character_avatar_map.json"
NIKKE_OBFUSCATION_PRIMES = [224737, 1000639, 2654435761, 2654435769, 1000621, 4294967291]
UNION_RAID_TEAM_IMAGE_WIDTH = 180
UNION_RAID_TEAM_IMAGE_UNIT = 36
UNION_RAID_TEAM_IMAGE_CLOUDINARY_BASE = "https://res.cloudinary.com/dfzoxtcki/image/upload"


def _signed_32(value: int) -> int:
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value & 0x80000000 else value


def _nikke_djb2_mod(text: str, mod_value: int) -> int:
    value = mod_value
    for char in text:
        value = _signed_32(value * 33 + ord(char))
    return value


def _nikke_two_letter_hash(text: str, mod_value: int) -> str:
    result = ((_nikke_djb2_mod(text, mod_value) % mod_value) + mod_value) % mod_value
    return chr(97 + (result // 26) % 26) + chr(97 + result % 26)


def _nikke_two_number_hash(text: str, mod_value: int) -> str:
    result = ((_nikke_djb2_mod(text, mod_value) % mod_value) + mod_value) % mod_value
    return str(result % 99).zfill(2)


def _obfuscate_nikke_resource_path(path: str) -> str:
    raw_path = str(path or "").strip().lstrip("/")
    if not raw_path:
        return ""
    parts = [part for part in raw_path.split("/") if part]
    for idx, part in enumerate(parts):
        if idx == len(parts) - 1:
            if "." not in part:
                parts[idx] = hashlib.md5(raw_path.encode("utf-8")).hexdigest()
                continue
            _base, ext = part.rsplit(".", 1)
            parts[idx] = f"{hashlib.md5(raw_path.encode('utf-8')).hexdigest()}.{ext}"
            continue
        prime = NIKKE_OBFUSCATION_PRIMES[idx]
        parts[idx] = f"{_nikke_two_letter_hash(raw_path, prime)}-{_nikke_two_number_hash(raw_path, prime)}"
    return "/".join(parts)


def _nikke_static_resource_url(path: str) -> str:
    obfuscated = _obfuscate_nikke_resource_path(path)
    return f"{NIKKE_STATIC_RESOURCE_BASE_URL}/{obfuscated}" if obfuscated else ""


def _build_nikke_icon_url(resource_id: Any, costume_index: Any = 0) -> str:
    rid = _to_int(resource_id)
    skin = _to_int(costume_index)
    if rid is None:
        return ""
    if skin is None:
        skin = 0
    return _nikke_static_resource_url(f"character/si/si_c{rid:03d}_{skin:02d}_s.webp")


def _weserv_png_url(icon_url: str) -> str:
    raw = str(icon_url or "").strip()
    prefix = f"{NIKKE_STATIC_RESOURCE_BASE_URL}/"
    if raw.startswith(prefix):
        path = f"sg-tools-cdn.blablalink.com/{raw[len(prefix):]}"
    else:
        path = raw.replace("https://", "").replace("http://", "")
    return f"https://images.weserv.nl/?url={quote(path, safe='')}&output=png"


def _cloudinary_fetch_layer_url(icon_url: str) -> str:
    weserv_url = _weserv_png_url(icon_url)
    encoded = base64.b64encode(weserv_url.encode("utf-8")).decode("ascii").rstrip("=")
    return encoded.replace("+", "-").replace("/", "_")


def _build_union_raid_team_image_formula(entries: List[Dict[str, Any]], id_to_image_url: Dict[int, str]) -> str:
    rows: List[List[str]] = []
    for entry in entries:
        squad_ids = entry.get("squad_id", []) if isinstance(entry, dict) else []
        if not isinstance(squad_ids, list):
            squad_ids = []
        urls: List[str] = []
        for sid in squad_ids[:5]:
            sid_int = _to_int(sid)
            if sid_int is None:
                continue
            image_url = id_to_image_url.get(sid_int, "")
            if image_url:
                urls.append(image_url)
        if urls:
            rows.append(urls)

    if not rows:
        return ""

    canvas_height = UNION_RAID_TEAM_IMAGE_UNIT * len(rows)
    layers: List[str] = []
    fallback_url = rows[0][0]
    for row_idx, urls in enumerate(rows):
        for col_idx, image_url in enumerate(urls[:5]):
            layers.append(
                f"l_fetch:{_cloudinary_fetch_layer_url(image_url)}"
                f"/c_fill,w_{UNION_RAID_TEAM_IMAGE_UNIT},h_{UNION_RAID_TEAM_IMAGE_UNIT}"
                f"/fl_layer_apply,g_north_west,x_{col_idx * UNION_RAID_TEAM_IMAGE_UNIT},y_{row_idx * UNION_RAID_TEAM_IMAGE_UNIT}"
            )

    composite_url = (
        f"{UNION_RAID_TEAM_IMAGE_CLOUDINARY_BASE}/"
        f"f_auto,q_auto,c_fill,w_1,h_1/"
        f"c_pad,w_{UNION_RAID_TEAM_IMAGE_WIDTH},h_{canvas_height},g_north_west,b_transparent/"
        f"{'/'.join(layers)}/1x1_glmeeu"
    )
    return f'=IFERROR(IMAGE("{composite_url}"),IMAGE("{_weserv_png_url(fallback_url)}"))'


def _to_int(value: Any) -> Optional[int]:
    coerced = _coerce_number(value)
    if coerced is None or coerced == "":
        return None
    try:
        return int(float(coerced))
    except Exception:
        return None


def _format_damage_text(value: Any) -> str:
    coerced = _coerce_number(value)
    if coerced is None or coerced == "":
        return ""
    if isinstance(coerced, int):
        return f"{coerced:,}"
    if isinstance(coerced, float):
        if coerced.is_integer():
            return f"{int(coerced):,}"
        return str(coerced)
    text = str(coerced).strip()
    if text.startswith("'"):
        text = text[1:]
    return text


def _to_roman_text(value: Any) -> str:
    n = _to_int(value)
    if n is None:
        return ""
    if n <= 0:
        return str(n)
    return _to_roman(n)


def _to_roman(num: int) -> str:
    vals = [
        (1000, "M"),
        (900, "CM"),
        (500, "D"),
        (400, "CD"),
        (100, "C"),
        (90, "XC"),
        (50, "L"),
        (40, "XL"),
        (10, "X"),
        (9, "IX"),
        (5, "V"),
        (4, "IV"),
        (1, "I"),
    ]
    out = []
    n = num
    for val, sym in vals:
        while n >= val:
            out.append(sym)
            n -= val
    return "".join(out)


def _coerce_number(value: Any) -> Any:
    if value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip()
    if text.startswith("'"):
        text = text[1:]
    if text == "":
        return ""
    try:
        return int(text)
    except Exception:
        pass
    try:
        return float(text)
    except Exception:
        return value


def _build_web_member(item: Dict[str, Any], id_to_name: Dict[int, str]) -> Dict[str, Any]:
    member_name = str(item.get("username", "")).strip()
    data_list = item.get("data", [])
    if not isinstance(data_list, list):
        data_list = []
    attacks: List[Dict[str, Any]] = []
    merged_teams_by_step: Dict[str, Dict[str, Any]] = {}
    for attack_index in range(1, 4):
        if attack_index - 1 < len(data_list) and isinstance(data_list[attack_index - 1], dict):
            entry = data_list[attack_index - 1]
            squad_ids = entry.get("squad_id", [])
            raw_step = _to_int(entry.get("step"))
            step = _normalize_raid_boss_step(entry.get("step"))
            if raw_step is not None and step is None:
                continue
        else:
            squad_ids = []
            step = None
        if not isinstance(squad_ids, list):
            squad_ids = []
        team_names: List[str] = []
        for sid in squad_ids[:5]:
            try:
                sid_int = int(sid)
                name = id_to_name.get(sid_int) or str(sid)
            except Exception:
                name = str(sid)
            team_names.append(name)
        while len(team_names) < 5:
            team_names.append("")
        attacks.append({"attack_index": attack_index, "step": step, "team_names": team_names})
        if step is not None and 1 <= step <= 5:
            key = str(step)
            if key not in merged_teams_by_step:
                merged_teams_by_step[key] = {"team_names": [], "attack_indexes": []}
            merged_teams_by_step[key]["team_names"].extend(team_names)
            merged_teams_by_step[key]["attack_indexes"].append(attack_index)
    return {
        "member_name": member_name,
        "attacks": attacks,
        "merged_teams_by_step": merged_teams_by_step,
    }


def _summarize_web_members(members: List[Dict[str, Any]], limit: int = 2) -> List[Dict[str, Any]]:
    summaries: List[Dict[str, Any]] = []
    for member in members[:limit]:
        member_name = str(member.get("member_name", "")).strip()
        merged = member.get("merged_teams_by_step", {})
        step_summary: Dict[str, List[str]] = {}
        if isinstance(merged, dict):
            for step, data in merged.items():
                if not isinstance(data, dict):
                    continue
                raw_names = data.get("team_names", [])
                if not isinstance(raw_names, list):
                    continue
                names = [str(name).strip() for name in raw_names if str(name).strip()]
                if names:
                    step_summary[str(step)] = names[:5]
        summaries.append(
            {
                "member_name": member_name,
                "step_summary": step_summary,
            }
        )
    return summaries


def _build_row_update(row_idx: int, start_col: int, values: List[Any]) -> Dict[str, Any]:
    end_col = start_col + len(values) - 1
    start_letter = col_letter(start_col)
    end_letter = col_letter(end_col)
    rng = f"{start_letter}{row_idx}:{end_letter}{row_idx}"
    return {"range": rng, "values": [values]}


def _apply_batch_updates(ws, payload: List[Dict[str, Any]]) -> None:
    if not payload:
        return
    ws.batch_update(payload, value_input_option="RAW")


def _apply_formula_updates(ws, payload: List[Dict[str, Any]]) -> None:
    if not payload:
        return
    ws.batch_update(payload, value_input_option="USER_ENTERED")


def _load_union_data(path: Optional[Path]) -> List[Dict[str, Any]]:
    if not path or not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return []
    if not isinstance(data, dict):
        return []
    items = data.get("participate_data")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _fetch_json_list(url: str) -> List[Dict[str, Any]]:
    try:
        import requests
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return []
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code < 200 or resp.status_code >= 300:
            log_msg(f"union_upload fetch_json failed url={url} status={resp.status_code}")
            return []
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def _normalize_nikke_info_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        raw_id = item.get("id")
        nikke_id = int(str(raw_id)[:4])
    except Exception:
        return None
    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        name = item.get("name_localkey", {}).get("name") if isinstance(item.get("name_localkey"), dict) else ""
    return {
        "id": nikke_id,
        "name": str(name or "").strip(),
        "resource_id": _to_int(item.get("resource_id")),
    }


def _load_nikke_info_for_union_upload() -> List[Dict[str, Any]]:
    data = read_cloud_data_json(NIKKES_INFO_FILE, default=[])
    cloud_items = [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []
    normalized: Dict[int, Dict[str, Any]] = {}
    for item in cloud_items:
        mapped = _normalize_nikke_info_item(item)
        if mapped is not None:
            normalized[int(mapped["id"])] = mapped

    if not normalized or any(item.get("resource_id") is None for item in normalized.values()):
        for item in _fetch_json_list(NIKKES_INFO_URL):
            mapped = _normalize_nikke_info_item(item)
            if mapped is None:
                continue
            current = normalized.get(int(mapped["id"]))
            if current is None:
                normalized[int(mapped["id"])] = mapped
            else:
                if not current.get("name") and mapped.get("name"):
                    current["name"] = mapped["name"]
                if current.get("resource_id") is None and mapped.get("resource_id") is not None:
                    current["resource_id"] = mapped["resource_id"]

    return list(normalized.values())


def _load_nikke_avatar_map() -> List[Dict[str, Any]]:
    return _fetch_json_list(_nikke_static_resource_url(NIKKE_AVATAR_MAP_PATH))


def _load_nikke_upload_maps() -> Tuple[Dict[int, str], Dict[int, str]]:
    info_items = _load_nikke_info_for_union_upload()
    id_to_name: Dict[int, str] = {}
    id_to_image_url: Dict[int, str] = {}
    resource_to_name: Dict[int, str] = {}
    resource_default_image: Dict[int, str] = {}

    for item in info_items:
        nikke_id = _to_int(item.get("id"))
        if nikke_id is None:
            continue
        name = str(item.get("name", "") or "").strip()
        resource_id = _to_int(item.get("resource_id"))
        if name:
            id_to_name[nikke_id] = name
        if resource_id is not None:
            if name:
                resource_to_name[resource_id] = name
            resource_default_image[resource_id] = _build_nikke_icon_url(resource_id, 0)
            id_to_image_url[nikke_id] = resource_default_image[resource_id]

    for item in _load_nikke_avatar_map():
        avatar_id = _to_int(item.get("id"))
        resource_id = _to_int(item.get("resource_id"))
        costume_index = _to_int(item.get("costume_index"))
        if avatar_id is None or resource_id is None:
            continue
        short_avatar_id = int(str(avatar_id)[:4])
        icon_url = _build_nikke_icon_url(resource_id, costume_index if costume_index is not None else 0)
        if icon_url:
            id_to_image_url[avatar_id] = icon_url
            if short_avatar_id not in id_to_image_url:
                id_to_image_url[short_avatar_id] = icon_url
        name = resource_to_name.get(resource_id, "")
        if name and short_avatar_id not in id_to_name:
            id_to_name[short_avatar_id] = name

    for item in info_items:
        nikke_id = _to_int(item.get("id"))
        resource_id = _to_int(item.get("resource_id"))
        if nikke_id is None or resource_id is None:
            continue
        if nikke_id not in id_to_image_url and resource_id in resource_default_image:
            id_to_image_url[nikke_id] = resource_default_image[resource_id]

    return id_to_name, id_to_image_url


def _load_nikke_id_map() -> Dict[int, str]:
    id_to_name, _id_to_image_url = _load_nikke_upload_maps()
    return id_to_name


def _resolve_raid_plan_sheet_for_union_upload(upload_sheet_id: str) -> Tuple[str, str]:
    """Return the CharacterDetails sheet that owns the uploaded UnionRaid sheet."""
    target_sheet_id = str(upload_sheet_id or "").strip()
    if not target_sheet_id:
        return "", ""

    try:
        sheets, _ = load_sheets()
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return target_sheet_id, ""

    union_id = ""
    for item in sheets:
        if not isinstance(item, dict):
            continue
        item_sheet_id = str(item.get("sheet_id", "") or "").strip()
        item_type = str(item.get("type", "") or "").strip()
        if item_sheet_id != target_sheet_id:
            continue
        union_id = str(item.get("union_id", "") or "").strip()
        if item_type == CHAR_DETAILS_TYPE:
            return target_sheet_id, union_id
        if item_type != UNION_RAID_TYPE:
            return target_sheet_id, union_id
        break

    if not union_id:
        return target_sheet_id, ""

    for item in sheets:
        if not isinstance(item, dict):
            continue
        if str(item.get("type", "") or "").strip() != CHAR_DETAILS_TYPE:
            continue
        if str(item.get("union_id", "") or "").strip() != union_id:
            continue
        raid_plan_sheet_id = str(item.get("sheet_id", "") or "").strip()
        if raid_plan_sheet_id:
            return raid_plan_sheet_id, union_id

    return target_sheet_id, union_id


def _notify_union_web_app(
    month: str,
    sheet_id: str,
    members: List[Dict[str, Any]],
    *,
    damage_sheet_id: str = "",
    union_id: str = "",
) -> Tuple[bool, str]:
    if not UNION_WEB_APP_URL:
        return False, "?芾身摰?UNION_WEB_APP_URL"
    payload = {
        "method": "sync_union_raid_teams",
        "month": month,
        "spreadsheet_id": sheet_id,
        "members": members,
    }
    damage_sheet_id = str(damage_sheet_id or "").strip()
    if damage_sheet_id and damage_sheet_id != sheet_id:
        payload["damage_spreadsheet_id"] = damage_sheet_id
    union_id = str(union_id or "").strip()
    if union_id:
        payload["union_id"] = union_id
    try:
        import requests
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return False, "requests ?臬憭望?"

    try:
        resp = requests.post(UNION_WEB_APP_URL, json=payload, timeout=600)
        body_text = (resp.text or "").strip()
        parsed = None
        try:
            parsed = resp.json()
        except Exception:
            parsed = None

        if 200 <= resp.status_code < 300:
            if body_text.lower() in {"unauthorized", "bad_request", "busy"} or body_text.lower().startswith("error:"):
                return False, body_text[:300]
            if isinstance(parsed, dict) and parsed.get("ok") is False:
                err = str(parsed.get("error") or parsed.get("message") or body_text or "web app ok=false")
                return False, err
            if isinstance(parsed, dict):
                updated = parsed.get("updated_spreadsheets") or []
                total_cells = 0
                for item in updated:
                    try:
                        total_cells += int(item.get("updated_boss_cells", 0) or 0)
                    except Exception:
                        pass
                if updated and total_cells == 0:
                    return False, "WebApp 無更新任何格子"
            return True, body_text[:300]
        return False, f"HTTP {resp.status_code} {body_text[:300]}"
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return False, str(exc)


def _get_sheet_titles(sheet_id: str) -> Optional[List[str]]:
    if not sheet_id:
        return None
    try:
        cli = get_gspread_client()
        sh = cli.open_by_key(sheet_id)
        return [ws.title for ws in sh.worksheets()]
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return None


def _extract_months_from_titles(titles: List[str]) -> List[str]:
    seen = set()
    months: List[str] = []
    for title in titles:
        if not isinstance(title, str):
            continue
        m = re.match(r"^(\d{4}/\d{2})", title)
        if not m:
            continue
        month = m.group(1)
        if month in seen:
            continue
        seen.add(month)
        months.append(month)
    return months


def _list_union_files() -> List[Path]:
    folder = UNION_RAID_TEMP_FILE.parent
    if not folder.exists():
        return []
    files = [p for p in folder.glob("*.json") if p.is_file()]
    files.sort(key=lambda p: p.name, reverse=True)
    return files


def _get_selected_index(listbox: tk.Listbox) -> Optional[int]:
    idxs = listbox.curselection()
    return idxs[0] if idxs else None


__all__ = ["open_upload_union_dialog", "col_letter"]
