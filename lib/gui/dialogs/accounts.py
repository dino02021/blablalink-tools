# -*- coding: utf-8 -*-
"""選擇/管理帳號對話框"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Dict, List

from ..layout import (
    create_dialog,
    close_dialog,
    confirm_dialog,
    create_frame,
    create_btn_frame,
    create_btn_between,
    create_btn_last,
    create_msg_label,
    create_entry_label,
    create_entry,
    create_entry_frame,
    create_listbox,
)
from ...utils import save_accounts


def choose_or_manage_accounts(parent: tk.Misc, accounts: List[Dict[str, str]]) -> Dict[str, str] | None:
    """彈出選擇/管理帳號對話框，返回選中的帳號或 None。"""
    dlg = create_dialog(parent, "選擇/管理帳號", 360, 320)

    frame = create_frame(dlg)
    frame.rowconfigure(0, weight=1)
    listbox = create_listbox(frame, row=0, column=0)
    _refresh_list(listbox, accounts)

    result: Dict[str, Dict[str, str] | None] = {"selected": None}

    listbox.bind(
        "<Double-Button-1>", lambda e=None: _apply_and_close(listbox, accounts, msg_var, msg_label, result, dlg)
    )

    btns = create_btn_frame(frame, row=1, column=0)
    btns.columnconfigure(0, weight=1)
    btns.columnconfigure(1, weight=0)
    btns.columnconfigure(2, weight=0)

    create_btn_between(
        btns,
        "套用選擇",
        lambda: _apply_and_close(listbox, accounts, msg_var, msg_label, result, dlg),
        row=0,
        column=0,
        sticky="we",
    )
    create_btn_between(
        btns,
        "新增帳號",
        lambda: _open_add_account(dlg, accounts, listbox, msg_var, msg_label),
        row=0,
        column=1,
        sticky="e",
    )
    create_btn_last(
        btns,
        "刪除帳號",
        lambda: _delete_accounts(listbox, accounts, msg_var, msg_label),
        row=0,
        column=2,
        sticky="e",
    )
    msg_var = tk.StringVar(value="")
    msg_label = create_msg_label(frame, msg_var, row=2, column=0, sticky="w")
    msg_label.configure(foreground="green", anchor="w", justify="left")

    dlg.protocol("WM_DELETE_WINDOW", lambda: close_dialog(dlg))
    parent.wait_window(dlg)
    return result["selected"]


# ---------------- helper ----------------

def _refresh_list(listbox: tk.Listbox, accounts: List[Dict[str, str]]) -> None:
    listbox.delete(0, "end")
    for acc in accounts:
        name = acc.get("nickname") or acc.get("username", "")
        listbox.insert("end", f" {name}")


def _get_selected_index(listbox: tk.Listbox) -> int | None:
    idxs = listbox.curselection()
    return idxs[0] if idxs else None


def _delete_accounts(listbox: tk.Listbox, accounts: List[Dict[str, str]], msg_var: tk.StringVar, msg_label: ttk.Label) -> None:
    idxs = list(listbox.curselection())
    if not idxs:
        msg_var.set("請先選擇要刪除的帳號")
        msg_label.configure(foreground="red")
        return
    if not confirm_dialog(listbox.winfo_toplevel(), "確定要刪除選取的帳號嗎？"):
        return
    for idx in reversed(idxs):
        del accounts[idx]
    save_accounts(accounts)
    _refresh_list(listbox, accounts)
    msg_var.set("已刪除帳號")
    msg_label.configure(foreground="green")


def _save_new_account(
    nick_var: tk.StringVar,
    usr_var: tk.StringVar,
    pwd_var: tk.StringVar,
    accounts: List[Dict[str, str]],
    listbox: tk.Listbox,
    warn_var: tk.StringVar,
    msg_var: tk.StringVar | None,
    msg_label: ttk.Label | None,
    dlg_add: tk.Toplevel,
) -> None:
    nick = nick_var.get().strip()
    usr = usr_var.get().strip()
    pwd = pwd_var.get().strip()
    if not nick or not usr or not pwd:
        warn_var.set("暱稱、帳號、密碼都要填寫")
        return
    accounts.append({"nickname": nick, "username": usr, "password": pwd})
    save_accounts(accounts)
    _refresh_list(listbox, accounts)
    if msg_var is not None:
        msg_var.set("已新增帳號")
    if msg_label is not None:
        msg_label.configure(foreground="green")
    close_dialog(dlg_add)


def _open_add_account(
    parent: tk.Toplevel,
    accounts: List[Dict[str, str]],
    listbox: tk.Listbox,
    msg_var_parent: tk.StringVar,
    msg_label_parent: ttk.Label,
) -> None:
    dlg_add = create_dialog(parent, "新增帳號", 320, 200)
    dlg_add.protocol("WM_DELETE_WINDOW", lambda: close_dialog(dlg_add))

    frm = create_frame(dlg_add)
    entry_frame = create_entry_frame(frm, row=0, column=0)
    create_entry_label(entry_frame, "暱稱", row=0, column=0)
    nick_var = tk.StringVar()
    create_entry(entry_frame, nick_var, row=0, column=1)
    create_entry_label(entry_frame, "帳號", row=1, column=0)
    usr_var = tk.StringVar()
    create_entry(entry_frame, usr_var, row=1, column=1)
    create_entry_label(entry_frame, "密碼", row=2, column=0)
    pwd_var = tk.StringVar()
    create_entry(entry_frame, pwd_var, row=2, column=1, show="*")

    btns = create_btn_frame(frm, row=1, column=0)
    btns.columnconfigure(0, weight=1)
    create_btn_last(
        btns,
        "儲存",
        lambda: _save_new_account(
            nick_var, usr_var, pwd_var, accounts, listbox, warn_var, msg_var_parent, msg_label_parent, dlg_add
        ),
        row=0,
        column=0,
        sticky="we",
    )
    
    warn_var = tk.StringVar(value="")
    warn_label = create_msg_label(frm, warn_var, row=2, column=0, sticky="w")
    warn_label.configure(foreground="red", anchor="w", justify="left")

    dlg_add.grab_set()
    dlg_add.wait_window(dlg_add)


def _apply_and_close(
    listbox: tk.Listbox,
    accounts: List[Dict[str, str]],
    msg_var: tk.StringVar,
    msg_label: ttk.Label,
    result: Dict[str, Dict[str, str] | None],
    dlg: tk.Toplevel,
) -> None:
    idx = _get_selected_index(listbox)
    if idx is None:
        msg_var.set("請先選擇帳號")
        msg_label.configure(foreground="red")
        return
    result["selected"] = accounts[idx]
    close_dialog(dlg)

