# -*- coding: utf-8 -*-
"""妮姬資料批次下載：對話框與成員管理。"""

from __future__ import annotations

import base64
import functools
import threading
from typing import List, Tuple

import tkinter as tk
from tkinter import ttk

from ...log import log_exc, log_msg
from ...utils import load_session, run_with_session_context
from ...api import fetch_user_character_details, fetch_user_characters
from ...data_store import save_members
from ..layout import (
    close_dialog,
    confirm_dialog,
    create_btn_between,
    create_btn_frame,
    create_btn_last,
    create_dialog,
    create_entry,
    create_entry_frame,
    create_entry_label,
    create_frame,
    create_listbox,
    create_msg_label,
)
from ...data_store import prepare_checklist_items


def open_batch_download_nikke(app) -> None:
    """由主畫面呼叫：開啟批次下載視窗。"""
    if not getattr(app, "_last_slug", None):
        app._set_status("請先登入", "red")
        return
    dlg = create_dialog(app.root, "批次下載妮姬資料", 360, 340)
    frame = create_frame(dlg)
    frame.rowconfigure(0, weight=1)

    lb = create_listbox(frame, row=0, column=0)
    lb.configure(selectmode="extended")
    _refresh_batch_list(app, lb)

    status_var = tk.StringVar(value="")
    status_label = create_msg_label(frame, textvariable=status_var, row=2, column=0, sticky="w")
    status_label.configure(foreground="red", anchor="w", justify="left")

    btns = create_btn_frame(frame, row=1, column=0)
    btns.columnconfigure(0, weight=1)
    btns.columnconfigure(1, weight=0)
    btns.columnconfigure(2, weight=0)

    create_btn_between(
        btns,
        "選擇並下載",
        lambda: _batch_choose_download(app, lb, status_var, dlg),
        row=0,
        column=0,
        sticky="we",
    )
    create_btn_between(
        btns,
        "全部下載",
        lambda: _batch_download_all(app, status_var, dlg),
        row=0,
        column=1,
        sticky="e",
    )
    create_btn_last(
        btns,
        "管理成員",
        lambda: _open_manage_members(app, dlg, lb),
        row=0,
        column=2,
        sticky="e",
    )

    lb.bind("<Double-Button-1>", lambda _e=None: _batch_choose_download(app, lb, status_var, dlg))

    dlg.protocol("WM_DELETE_WINDOW", lambda: close_dialog(dlg))


def _batch_choose_download(app, lb: tk.Listbox, status_var: tk.StringVar, dlg: tk.Toplevel) -> None:
    sel = lb.curselection()
    if not sel:
        status_var.set("請選擇成員")
        return
    members = []
    for i in sel:
        if 0 <= i < len(app.members):
            members.append(app.members[i])
    _start_batch_download(app, members, status_var, dlg)


def _batch_download_all(app, status_var: tk.StringVar, dlg: tk.Toplevel) -> None:
    _start_batch_download(app, list(app.members), status_var, dlg)


def _start_batch_download(app, members, status_var: tk.StringVar, dlg: tk.Toplevel) -> None:
    if not members:
        status_var.set("請選擇成員")
        return
    status_var.set("")
    app.nikke_status_var.set("下載中...")
    app.nikke_status_label.configure(foreground="green")
    threading.Thread(target=_batch_download_nikke_worker, args=(app, members), daemon=True).start()
    close_dialog(dlg)


def _batch_download_nikke_worker(app, members) -> None:
    ok_count = 0
    total_count = len(members)
    msg = ""
    name_ok, checklist_items, prep_msg = prepare_checklist_items()
    if not name_ok:
        msg = prep_msg or "下載失敗"
    else:
        state = load_session(app._last_slug or "")
        if not state:
            msg = "找不到登入狀態"
        else:
            try:
                ok_count = app._run_coro_sync(functools.partial(_batch_download_async, state, members, checklist_items))
            except Exception as exc:  # noqa: BLE001
                log_exc(exc)
                msg = str(exc)

    app.root.after(0, functools.partial(_finish_batch_download, app, ok_count, total_count, msg))


async def _batch_download_async(state, members, checklist_items) -> int:
    return await run_with_session_context(
        state,
        functools.partial(_batch_download_with_ctx, members=members, checklist_items=checklist_items),
    )


async def _batch_download_with_ctx(ctx, members, checklist_items) -> int:
    ok_count = 0
    for mem in members:
        nickname = mem.get("nick_name")
        intl_id = mem.get("intl_open_id")
        if not nickname or not intl_id:
            continue
        name_codes = await fetch_user_characters(ctx, intl_open_id=intl_id)
        if not name_codes:
            continue
        filtered = _filter_checklist_items(checklist_items, name_codes)
        if not filtered:
            continue
        res = await fetch_user_character_details(ctx, filtered, nickname, intl_open_id=intl_id)
        if res:
            ok_count += 1
    return ok_count


def _finish_batch_download(app, ok_count: int, total_count: int, msg: str) -> None:
    if msg:
        app.nikke_status_var.set(f"下載失敗：{msg}")
        app.nikke_status_label.configure(foreground="red")
        return
    app.nikke_status_var.set(f"下載成功 ({ok_count}/{total_count})")
    app.nikke_status_label.configure(foreground="green")


def _open_manage_members(app, parent=None, batch_lb: tk.Listbox | None = None) -> None:
    dlg_parent = parent or app.root
    dlg = create_dialog(dlg_parent, "管理成員", 360, 320)
    frame = create_frame(dlg)
    frame.rowconfigure(0, weight=1)

    lb = create_listbox(frame, row=0, column=0)
    _refresh_member_list(app, lb)

    warn_var = tk.StringVar(value="")
    warn_label = create_msg_label(frame, textvariable=warn_var, row=2, column=0, sticky="w")
    warn_label.configure(foreground="red", anchor="w", justify="left")

    btns = create_btn_frame(frame, row=1, column=0)
    btns.columnconfigure(0, weight=1)
    btns.columnconfigure(1, weight=0)

    create_btn_between(
        btns,
        "新增成員",
        lambda: _open_add_member_dialog(app, dlg, lb, warn_var),
        row=0,
        column=0,
        sticky="we",
    )
    create_btn_last(
        btns,
        "刪除成員",
        lambda: _delete_member_selection(app, lb, warn_var),
        row=0,
        column=1,
        sticky="e",
    )

    dlg.protocol("WM_DELETE_WINDOW", lambda: _close_manage_members(dlg, app, batch_lb))


def _close_manage_members(dlg: tk.Toplevel, app, batch_lb: tk.Listbox | None) -> None:
    close_dialog(dlg)
    if batch_lb is None:
        return
    _refresh_batch_list(app, batch_lb)


def _refresh_member_list(app, lb: tk.Listbox) -> None:
    lb.delete(0, "end")
    for mem in app.members:
        lb.insert("end", f" {mem.get('nick_name', '')} | {mem.get('intl_open_id', '')}")


def _refresh_batch_list(app, lb: tk.Listbox) -> None:
    lb.delete(0, "end")
    for mem in app.members:
        nick = mem.get("nick_name", "")
        lb.insert("end", f" {nick}")


def _filter_checklist_items(
    checklist_items: List[Tuple[int, str]],
    name_codes: List,
) -> List[Tuple[int, str]]:
    try:
        code_set = {int(x) for x in name_codes}
    except Exception:
        code_set = set()
        for x in name_codes:
            try:
                code_set.add(int(x))
            except Exception:
                continue
    if not code_set:
        return []
    return [item for item in checklist_items if item[0] in code_set]


def _open_add_member_dialog(app, parent, lb: tk.Listbox, warn_var: tk.StringVar) -> None:
    dlg = create_dialog(parent, "新增成員", 320, 160)
    frame = create_frame(dlg)

    entry_frame = create_entry_frame(frame, row=0, column=0)
    create_entry_label(entry_frame, "成員", row=0, column=0)
    name_var = tk.StringVar()
    create_entry(entry_frame, name_var, row=0, column=1)
    create_entry_label(entry_frame, "ID", row=1, column=0)
    id_var = tk.StringVar()
    create_entry(entry_frame, id_var, row=1, column=1)

    status_var = tk.StringVar(value="")
    status_label = create_msg_label(frame, textvariable=status_var, row=2, column=0, sticky="w")
    status_label.configure(foreground="red", anchor="w", justify="left")

    btns = create_btn_frame(frame, row=1, column=0)
    btns.columnconfigure(0, weight=1)

    create_btn_last(
        btns,
        "新增",
        lambda: _save_new_member(app, name_var, id_var, status_var, dlg, lb, warn_var),
        row=0,
        column=0,
        sticky="we",
    )

    dlg.protocol("WM_DELETE_WINDOW", lambda: close_dialog(dlg))


def _save_new_member(app, name_var, id_var, status_var: tk.StringVar, dlg, lb: tk.Listbox, warn_var: tk.StringVar) -> None:
    nm = name_var.get().strip()
    iid = id_var.get().strip()
    if not nm or not iid:
        status_var.set("請填寫成員與ID")
        return
    try:
        padded = iid + "=" * (-len(iid) % 4)
        decoded = base64.b64decode(padded).decode("utf-8", errors="ignore")
        parts = decoded.split("-")
        if len(parts) >= 2 and parts[1]:
            iid = parts[1]
    except Exception:
        pass
    app.members.append({"nick_name": nm, "intl_open_id": iid})
    save_members(app.members)
    _refresh_member_list(app, lb)
    warn_var.set("")
    close_dialog(dlg)


def _delete_member_selection(app, lb: tk.Listbox, warn_var: tk.StringVar) -> None:
    sel = list(lb.curselection())
    if not sel:
        warn_var.set("請選擇要刪除的成員")
        return
    if not confirm_dialog(lb.winfo_toplevel(), "確定要刪除選取的成員嗎？"):
        return
    for idx in reversed(sel):
        if 0 <= idx < len(app.members):
            app.members.pop(idx)
    save_members(app.members)
    _refresh_member_list(app, lb)
    warn_var.set("")

