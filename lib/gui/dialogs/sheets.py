# -*- coding: utf-8 -*-
"""管理試算表與類型的對話窗，佈局採用共用常數。"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Dict, List, Tuple

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
    create_combobox,
    create_msg_label,
    create_listbox,
)
from ...log import log_exc


def manage_sheets(
    parent: tk.Misc,
    gap: int,
    sheets: List[Dict[str, str]],
    sheet_types: List[str],
    title: str = "管理試算表",
) -> Tuple[List[Dict[str, str]], Dict[str, str] | None]:
    """管理試算表資料並返回更新後的清單。"""
    dlg = create_dialog(parent, title, 340, 300)

    frame = create_frame(dlg)
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(0, weight=1)

    listbox = create_listbox(frame, row=0, column=0)

    btns = create_btn_frame(frame, row=1, column=0)
    btns.columnconfigure(0, weight=1)
    btns.columnconfigure(1, weight=0)
    btns.columnconfigure(2, weight=0)

    msg_var = tk.StringVar(value="")
    msg_label = create_msg_label(frame, msg_var, row=2, column=0, sticky="w")
    msg_label.configure(foreground="green", anchor="w", justify="left")

    create_btn_between(
        btns,
        "新增試算表",
        lambda: _open_add_sheet_dialog(dlg, sheet_types, sheets, listbox, msg_var, msg_label),
        row=0,
        column=0,
        sticky="we",
    )
    create_btn_between(
        btns,
        "管理類型",
        lambda: _open_manage_types(dlg, gap, sheet_types),
        row=0,
        column=1,
        sticky="e",
    )
    create_btn_last(
        btns,
        "刪除試算表",
        lambda: _delete_sheet(listbox, sheets, msg_var, msg_label),
        row=0,
        column=2,
        sticky="e",
    )

    _refresh_sheets(listbox, sheets)

    dlg.protocol("WM_DELETE_WINDOW", lambda: close_dialog(dlg))
    dlg.grab_set()
    dlg.wait_window(dlg)
    return sheets, None


def manage_sheet_types(parent: tk.Misc, gap: int, sheet_types: List[str], title: str = "管理類型") -> List[str]:
    """管理類型清單，可新增/刪除後回傳更新結果。"""
    dlg = create_dialog(parent, title, 360, 280)

    frame = create_frame(dlg)
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(0, weight=1)

    listbox = create_listbox(frame, row=0, column=0)

    btns = create_btn_frame(frame, row=1, column=0)
    btns.columnconfigure(0, weight=1)
    btns.columnconfigure(1, weight=0)

    msg_var = tk.StringVar(value="")
    msg_label = create_msg_label(frame, msg_var, row=2, column=0, sticky="w")
    msg_label.configure(foreground="green", anchor="w", justify="left")

    create_btn_between(
        btns,
        "新增類型",
        lambda: _open_add_type_dialog(dlg, sheet_types, listbox, msg_var, msg_label),
        row=0,
        column=0,
        sticky="we",
    )
    create_btn_last(
        btns,
        "刪除類型",
        lambda: _delete_type(listbox, sheet_types, msg_var, msg_label),
        row=0,
        column=1,
        sticky="e",
    )

    _refresh_types(listbox, sheet_types)

    dlg.protocol("WM_DELETE_WINDOW", lambda: close_dialog(dlg))
    dlg.grab_set()
    dlg.wait_window(dlg)
    return sheet_types


# ---------------- helper functions ----------------


def _refresh_sheets(listbox: tk.Listbox, sheets: List[Dict[str, str]]) -> None:
    listbox.delete(0, "end")
    for item in sheets:
        stype = item.get("type", "")
        name = item.get("sheet_name", "")
        listbox.insert("end", f" {stype}-{name}")


def _get_selected_index(listbox: tk.Listbox) -> int | None:
    idxs = listbox.curselection()
    return idxs[0] if idxs else None


def _open_add_sheet_dialog(
    parent: tk.Toplevel,
    sheet_types: List[str],
    sheets: List[Dict[str, str]],
    listbox: tk.Listbox,
    msg_var: tk.StringVar,
    msg_label: ttk.Label,
) -> None:
    sub = create_dialog(parent, "新增試算表", 380, 240)
    sub_frame = create_frame(sub)
    entry_frame = create_entry_frame(sub_frame, row=0, column=0)
    entry_frame.columnconfigure(1, weight=1)
    create_entry_label(entry_frame, "類型", row=0, column=0)
    type_values = [f" {t}" for t in sheet_types] if sheet_types else []
    type_var = tk.StringVar(value=type_values[0] if type_values else "")
    type_box = create_combobox(entry_frame, type_var, type_values, row=0, column=1)
    if not sheet_types:
        type_box.configure(state="normal")

    create_entry_label(entry_frame, "名稱", row=1, column=0)
    name_var = tk.StringVar()
    create_entry(entry_frame, name_var, row=1, column=1)

    create_entry_label(entry_frame, "試算表ID", row=2, column=0)
    sheet_id_var = tk.StringVar()
    create_entry(entry_frame, sheet_id_var, row=2, column=1)

    create_entry_label(entry_frame, "聯盟ID", row=3, column=0)
    union_id_var = tk.StringVar()
    create_entry(entry_frame, union_id_var, row=3, column=1)

    sub_msg_var = tk.StringVar(value="")
    btns = create_btn_frame(sub_frame, row=1, column=0)
    btns.columnconfigure(0, weight=1)

    create_btn_last(
        btns,
        "儲存",
        lambda: _save_sheet(
            type_var,
            name_var,
            sheet_id_var,
            union_id_var,
            sheets,
            listbox,
            sub_msg_var,
            msg_var,
            msg_label,
            sub,
        ),
        row=0,
        column=0,
        sticky="we",
    )
    sub_msg_label = create_msg_label(sub_frame, sub_msg_var, row=2, column=0, sticky="w")
    sub_msg_label.configure(foreground="red", anchor="w", justify="left")

    sub.protocol("WM_DELETE_WINDOW", lambda: close_dialog(sub))
    sub.grab_set()
    sub.wait_window(sub)


def _delete_sheet(listbox: tk.Listbox, sheets: List[Dict[str, str]], msg_var: tk.StringVar, msg_label: ttk.Label) -> None:
    idx = _get_selected_index(listbox)
    if idx is None:
        msg_var.set("請先選擇要刪除的試算表")
        msg_label.configure(foreground="red")
        return
    if not confirm_dialog(listbox.winfo_toplevel(), "確定要刪除選取的試算表嗎？"):
        return
    del sheets[idx]
    _refresh_sheets(listbox, sheets)
    msg_var.set("已刪除試算表")
    msg_label.configure(foreground="green")


def _open_manage_types(parent: tk.Misc, gap: int, sheet_types: List[str]) -> None:
    try:
        new_types = manage_sheet_types(parent, gap, sheet_types)
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return
    sheet_types[:] = new_types


def _refresh_types(listbox: tk.Listbox, sheet_types: List[str]) -> None:
    listbox.delete(0, "end")
    for t in sheet_types:
        listbox.insert("end", f" {t}")


def _get_selected_type(listbox: tk.Listbox) -> int | None:
    sel = listbox.curselection()
    return sel[0] if sel else None


def _open_add_type_dialog(
    parent: tk.Toplevel, sheet_types: List[str], listbox: tk.Listbox, msg_var: tk.StringVar, msg_label: ttk.Label
) -> None:
    sub = create_dialog(parent, "新增類型", 260, 130)
    sub_frame = create_frame(sub)
    entry_frame = create_entry_frame(sub_frame, row=0, column=0)
    entry_frame.columnconfigure(1, weight=1)
    create_entry_label(entry_frame, "名稱", row=0, column=0)
    name_var = tk.StringVar()
    create_entry(entry_frame, name_var, row=0, column=1)

    sub_msg_var = tk.StringVar(value="")
    btns = create_btn_frame(sub_frame, row=1, column=0)
    btns.columnconfigure(0, weight=1)

    create_btn_last(
        btns,
        "儲存",
        lambda: _save_type(name_var, sheet_types, listbox, sub_msg_var, msg_var, msg_label, sub),
        row=0,
        column=0,
        sticky="we",
    )
    sub_msg = create_msg_label(sub_frame, sub_msg_var, row=2, column=0, sticky="w")
    sub_msg.configure(foreground="red", anchor="w", justify="left")

    sub.protocol("WM_DELETE_WINDOW", lambda: close_dialog(sub))
    sub.grab_set()
    sub.wait_window(sub)


def _delete_type(listbox: tk.Listbox, sheet_types: List[str], msg_var: tk.StringVar, msg_label: ttk.Label) -> None:
    idx = _get_selected_type(listbox)
    if idx is None:
        msg_var.set("請先選擇要刪除的類型")
        msg_label.configure(foreground="red")
        return
    if not confirm_dialog(listbox.winfo_toplevel(), "確定要刪除選取的類型嗎？"):
        return
    del sheet_types[idx]
    _refresh_types(listbox, sheet_types)
    msg_var.set("已刪除類型")
    msg_label.configure(foreground="green")


def _save_sheet(
    type_var: tk.StringVar,
    name_var: tk.StringVar,
    sheet_id_var: tk.StringVar,
    union_id_var: tk.StringVar,
    sheets: List[Dict[str, str]],
    listbox: tk.Listbox,
    sub_msg_var: tk.StringVar,
    msg_var: tk.StringVar,
    msg_label: ttk.Label,
    dlg: tk.Toplevel,
) -> None:
    stype_raw = type_var.get().strip()
    name = name_var.get().strip()
    sid = sheet_id_var.get().strip()
    union_id = union_id_var.get().strip()
    if not stype_raw or not name or not sid:
        sub_msg_var.set("請填寫類型、名稱與試算表ID")
        return
    if not union_id:
        sub_msg_var.set("聯盟ID為必填")
        return
    sheets.append(
        {
            "type": stype_raw,
            "sheet_name": name,
            "sheet_id": sid,
            "union_id": union_id,
        }
    )
    _refresh_sheets(listbox, sheets)
    msg_var.set("已新增試算表")
    msg_label.configure(foreground="green")
    close_dialog(dlg)


def _save_type(
    name_var: tk.StringVar,
    sheet_types: List[str],
    listbox: tk.Listbox,
    sub_msg_var: tk.StringVar,
    msg_var: tk.StringVar,
    msg_label: ttk.Label,
    dlg: tk.Toplevel,
) -> None:
    name = name_var.get().strip()
    if not name:
        sub_msg_var.set("請輸入名稱")
        return
    sheet_types.append(name)
    _refresh_types(listbox, sheet_types)
    msg_var.set("已新增類型")
    msg_label.configure(foreground="green")
    close_dialog(dlg)
