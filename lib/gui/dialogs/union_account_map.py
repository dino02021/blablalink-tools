# -*- coding: utf-8 -*-
"""管理聯盟對應雲端帳號的對話窗。"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Dict, List

from ..layout import (
    close_dialog,
    confirm_dialog,
    create_btn_frame,
    create_btn_between,
    create_btn_last,
    create_combobox,
    create_dialog,
    create_entry,
    create_entry_frame,
    create_entry_label,
    create_frame,
    create_listbox,
    create_msg_label,
)
from ...utils import load_accounts


def manage_union_account_map(
    parent: tk.Misc,
    items: List[Dict[str, str]],
    title: str = "管理雲端帳號",
) -> List[Dict[str, str]]:
    """管理 union_id 與帳號 username 對應。"""
    dlg = create_dialog(parent, title, 360, 320)

    frame = create_frame(dlg)
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(0, weight=1)

    listbox = create_listbox(frame, row=0, column=0)

    btns = create_btn_frame(frame, row=1, column=0)
    btns.columnconfigure(0, weight=1)
    btns.columnconfigure(1, weight=1)

    msg_var = tk.StringVar(value="")
    msg_label = create_msg_label(frame, msg_var, row=2, column=0, sticky="w")
    msg_label.configure(foreground="green", anchor="w", justify="left")

    create_btn_between(
        btns,
        "新增",
        lambda: _open_add_dialog(dlg, items, listbox, msg_var, msg_label),
        row=0,
        column=0,
        sticky="we",
    )
    create_btn_last(
        btns,
        "刪除",
        lambda: _delete_item(dlg, items, listbox, msg_var, msg_label),
        row=0,
        column=1,
        sticky="we",
    )

    _refresh_items(listbox, items)

    dlg.protocol("WM_DELETE_WINDOW", lambda: close_dialog(dlg))
    dlg.grab_set()
    dlg.wait_window(dlg)
    return items


def _refresh_items(listbox: tk.Listbox, items: List[Dict[str, str]]) -> None:
    listbox.delete(0, "end")
    account_lookup: Dict[str, str] = {}
    for account in load_accounts():
        username = str(account.get("username", "")).strip()
        nickname = str(account.get("nickname", "")).strip()
        if username:
            account_lookup[username] = nickname
    for item in items:
        union_id = str(item.get("union_id", "")).strip()
        username = str(item.get("username", "")).strip()
        nickname = account_lookup.get(username, "").strip()
        if nickname:
            listbox.insert("end", f" {union_id} | {nickname}")
        else:
            listbox.insert("end", f" {union_id}")


def _get_selected_index(listbox: tk.Listbox) -> int | None:
    selection = listbox.curselection()
    return selection[0] if selection else None


def _open_add_dialog(
    parent: tk.Toplevel,
    items: List[Dict[str, str]],
    listbox: tk.Listbox,
    msg_var: tk.StringVar,
    msg_label: ttk.Label,
) -> None:
    accounts = load_accounts()
    account_options = []
    option_to_username: Dict[str, str] = {}
    for account in accounts:
        nickname = str(account.get("nickname", "")).strip()
        username = str(account.get("username", "")).strip()
        if not username:
            continue
        label = f" {nickname} | {username}" if nickname else f" {username}"
        account_options.append(label)
        option_to_username[label] = username

    sub = create_dialog(parent, "新增雲端帳號", 400, 165)
    sub_frame = create_frame(sub)

    entry_frame = create_entry_frame(sub_frame, row=0, column=0)
    entry_frame.columnconfigure(1, weight=1)

    create_entry_label(entry_frame, "聯盟ID", row=0, column=0)
    union_id_var = tk.StringVar()
    create_entry(entry_frame, union_id_var, row=0, column=1)

    create_entry_label(entry_frame, "雲端帳號", row=1, column=0)
    account_var = tk.StringVar(value=account_options[0] if account_options else "")
    create_combobox(entry_frame, account_var, account_options, row=1, column=1)

    sub_msg_var = tk.StringVar(value="")
    sub_msg_label = create_msg_label(sub_frame, sub_msg_var, row=2, column=0, sticky="w")
    sub_msg_label.configure(foreground="red", anchor="w", justify="left")

    btns = create_btn_frame(sub_frame, row=1, column=0)
    btns.columnconfigure(0, weight=1)
    create_btn_last(
        btns,
        "儲存",
        lambda: _save_item(
            union_id_var,
            account_var,
            option_to_username,
            items,
            listbox,
            msg_var,
            msg_label,
            sub_msg_var,
            sub,
        ),
        row=0,
        column=0,
        sticky="we",
    )

    if not account_options:
        sub_msg_var.set("accounts.json 找不到可用帳號")

    sub.protocol("WM_DELETE_WINDOW", lambda: close_dialog(sub))
    sub.grab_set()
    sub.wait_window(sub)


def _save_item(
    union_id_var: tk.StringVar,
    account_var: tk.StringVar,
    option_to_username: Dict[str, str],
    items: List[Dict[str, str]],
    listbox: tk.Listbox,
    msg_var: tk.StringVar,
    msg_label: ttk.Label,
    sub_msg_var: tk.StringVar,
    dlg: tk.Toplevel,
) -> None:
    union_id = union_id_var.get().strip()
    selected = account_var.get()
    username = option_to_username.get(selected, "").strip()

    if not union_id:
        sub_msg_var.set("請輸入聯盟ID")
        return
    if not username:
        sub_msg_var.set("請選擇雲端帳號")
        return
    if any(str(item.get("union_id", "")).strip() == union_id for item in items):
        sub_msg_var.set("此聯盟ID已存在")
        return

    items.append(
        {
            "union_id": union_id,
            "username": username,
        },
    )
    _refresh_items(listbox, items)
    msg_var.set("已新增雲端帳號")
    msg_label.configure(foreground="green")
    close_dialog(dlg)


def _delete_item(
    parent: tk.Toplevel,
    items: List[Dict[str, str]],
    listbox: tk.Listbox,
    msg_var: tk.StringVar,
    msg_label: ttk.Label,
) -> None:
    idx = _get_selected_index(listbox)
    if idx is None:
        msg_var.set("請先選擇要刪除的聯盟ID")
        msg_label.configure(foreground="red")
        return
    if not confirm_dialog(parent, "確定要刪除選取的雲端帳號對應嗎？"):
        return
    del items[idx]
    _refresh_items(listbox, items)
    msg_var.set("已刪除雲端帳號對應")
    msg_label.configure(foreground="green")
