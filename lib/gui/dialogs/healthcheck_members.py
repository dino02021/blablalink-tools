# -*- coding: utf-8 -*-
"""Dialogs for managing Nikke healthcheck members."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Dict, List, Optional

from ..layout import (
    close_dialog,
    confirm_dialog,
    create_btn_between,
    create_btn_frame,
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
from ...data_store import (
    load_healthcheck_display_items,
    load_healthcheck_members,
    load_nikkes_info,
    save_healthcheck_members,
)

ALL_FILTER = "全部"
TYPE_VALUES = ["輸出", "輔助"]


def open_healthcheck_members_dialog(parent: tk.Misc) -> Optional[Dict[str, Any]]:
    """Open healthcheck member list management dialog."""
    dlg = create_dialog(parent, "健檢妮姬名單", 420, 360)

    frame = create_frame(dlg)
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(0, weight=1)

    listbox = create_listbox(frame, row=0, column=0)

    btn_frame = create_btn_frame(frame, row=1, column=0)
    btn_frame.columnconfigure(0, weight=1)
    btn_frame.columnconfigure(1, weight=0)
    btn_frame.columnconfigure(2, weight=0)

    msg_var = tk.StringVar(value="")
    msg_label = create_msg_label(frame, msg_var, row=2, column=0, sticky="w")
    msg_label.configure(foreground="green", anchor="w", justify="left")

    state: Dict[str, Any] = {"rows": []}
    result: Dict[str, Any] = {"ok": False, "message": ""}

    def _refresh() -> None:
        rows = load_healthcheck_display_items()
        state["rows"] = rows
        listbox.delete(0, "end")
        for item in rows:
            listbox.insert("end", f" {item['display']}")

    def _on_add() -> None:
        add_result = open_add_healthcheck_member_dialog(dlg)
        if not add_result:
            return
        _refresh()
        msg_var.set(str(add_result.get("message", "已新增健檢妮姬")))
        msg_label.configure(foreground="green")
        result["ok"] = True
        result["message"] = msg_var.get()

    def _on_edit() -> None:
        selected = listbox.curselection()
        if not selected:
            msg_var.set("請先選擇要編輯的健檢妮姬")
            msg_label.configure(foreground="red")
            return
        idx = int(selected[0])
        rows: List[Dict[str, Any]] = state.get("rows", [])
        if idx < 0 or idx >= len(rows):
            msg_var.set("選取項目無效")
            msg_label.configure(foreground="red")
            return

        edit_result = open_edit_healthcheck_member_dialog(dlg, rows[idx])
        if not edit_result:
            return
        _refresh()
        msg_var.set(str(edit_result.get("message", "已更新健檢妮姬類型")))
        msg_label.configure(foreground="green")
        result["ok"] = True
        result["message"] = msg_var.get()

    def _on_delete() -> None:
        selected = listbox.curselection()
        if not selected:
            msg_var.set("請先選擇要刪除的健檢妮姬")
            msg_label.configure(foreground="red")
            return
        idx = int(selected[0])
        rows: List[Dict[str, Any]] = state.get("rows", [])
        if idx < 0 or idx >= len(rows):
            msg_var.set("選取項目無效")
            msg_label.configure(foreground="red")
            return
        row = rows[idx]
        if not confirm_dialog(dlg, f"確定刪除 {row['display']} 嗎？"):
            return

        kept = [
            item
            for item in load_healthcheck_members()
            if not (int(item.get("name_code")) == int(row["name_code"]) and str(item.get("type")) == str(row["type"]))
        ]
        save_healthcheck_members(kept)
        _refresh()
        msg_var.set("已刪除健檢妮姬")
        msg_label.configure(foreground="green")
        result["ok"] = True
        result["message"] = msg_var.get()

    create_btn_between(btn_frame, "新增", _on_add, row=0, column=0, sticky="we")
    create_btn_between(btn_frame, "編輯", _on_edit, row=0, column=1, sticky="e")
    create_btn_last(btn_frame, "刪除", _on_delete, row=0, column=2, sticky="e")

    _refresh()
    dlg.protocol("WM_DELETE_WINDOW", lambda: close_dialog(dlg))
    dlg.wait_window(dlg)
    return result if result["ok"] else None


def open_add_healthcheck_member_dialog(parent: tk.Misc) -> Optional[Dict[str, Any]]:
    """Open add healthcheck member dialog."""
    dlg = create_dialog(parent, "新增健檢妮姬", 420, 560)

    frame = create_frame(dlg)
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(1, weight=1)

    nikkes = load_nikkes_info()

    filter_frame = create_entry_frame(frame, row=0, column=0)
    filter_frame.columnconfigure(1, weight=1)

    filter_specs = [
        ("class", "Class"),
        ("corporation", "Corporation"),
        ("element", "Element"),
        ("weapon_type", "Weapon"),
        ("use_burst_skill", "Burst"),
    ]

    filter_vars: Dict[str, tk.StringVar] = {}
    for idx, (field, label_text) in enumerate(filter_specs):
        row = idx
        create_entry_label(filter_frame, f"{label_text}:", row=row, column=0)
        values = sorted(
            {
                str(item.get(field, "") or "").strip()
                for item in nikkes
                if str(item.get(field, "") or "").strip()
            }
        )
        var = tk.StringVar(value=ALL_FILTER)
        create_combobox(filter_frame, var, [ALL_FILTER] + values, row=row, column=1)
        filter_vars[field] = var

    listbox = create_listbox(frame, row=1, column=0)

    type_frame = create_entry_frame(frame, row=2, column=0)
    type_frame.columnconfigure(1, weight=1)
    create_entry_label(type_frame, "Type:", row=0, column=0)
    type_var = tk.StringVar(value="")
    create_combobox(type_frame, type_var, TYPE_VALUES, row=0, column=1)

    btn_frame = create_btn_frame(frame, row=3, column=0)
    btn_frame.columnconfigure(0, weight=1)
    btn_frame.columnconfigure(1, weight=1)

    msg_var = tk.StringVar(value="")
    msg_label = create_msg_label(frame, msg_var, row=4, column=0, sticky="w")
    msg_label.configure(foreground="red", anchor="w", justify="left")

    state: Dict[str, Any] = {"rows": []}
    result: Dict[str, Any] = {"ok": False, "message": ""}

    def _passes_filters(item: Dict[str, Any]) -> bool:
        for field, _label in filter_specs:
            selected = filter_vars[field].get().strip()
            if not selected or selected == ALL_FILTER:
                continue
            if str(item.get(field, "") or "").strip() != selected:
                return False
        return True

    def _refresh_list() -> None:
        rows = [item for item in nikkes if _passes_filters(item)]
        rows.sort(key=lambda item: str(item.get("name", "") or ""))
        state["rows"] = rows
        listbox.delete(0, "end")
        for item in rows:
            name = str(item.get("name", "") or "").strip()
            listbox.insert("end", f" {name}")

    def _on_filter_change(*_args: Any) -> None:
        _refresh_list()

    def _on_save() -> None:
        selected = listbox.curselection()
        if not selected:
            msg_var.set("請先選擇妮姬")
            return
        member_type = type_var.get().strip()
        if member_type not in TYPE_VALUES:
            msg_var.set("請先選擇 type")
            return

        idx = int(selected[0])
        rows: List[Dict[str, Any]] = state.get("rows", [])
        if idx < 0 or idx >= len(rows):
            msg_var.set("選取角色無效")
            return
        row = rows[idx]
        try:
            name_code = int(row.get("name_code"))
        except Exception:
            msg_var.set("角色缺少有效 name_code")
            return

        items = load_healthcheck_members()
        key = (name_code, member_type)
        existing_keys = {(int(item.get("name_code")), str(item.get("type"))) for item in items}
        if key in existing_keys:
            msg_var.set("相同妮姬與 type 已存在")
            return

        items.append({"name_code": name_code, "type": member_type})
        save_healthcheck_members(items)
        result["ok"] = True
        result["message"] = "已新增健檢妮姬"
        close_dialog(dlg)

    create_btn_between(btn_frame, "新增", _on_save, row=0, column=0, sticky="we")
    create_btn_last(btn_frame, "取消", lambda: close_dialog(dlg), row=0, column=1, sticky="we")

    for var in filter_vars.values():
        var.trace_add("write", _on_filter_change)
    _refresh_list()

    dlg.protocol("WM_DELETE_WINDOW", lambda: close_dialog(dlg))
    dlg.wait_window(dlg)
    return result if result["ok"] else None


def open_edit_healthcheck_member_dialog(parent: tk.Misc, row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Open edit dialog for an existing healthcheck member type."""
    dlg = create_dialog(parent, "編輯健檢妮姬", 360, 180)

    frame = create_frame(dlg)
    frame.columnconfigure(0, weight=1)

    entry_frame = create_entry_frame(frame, row=0, column=0)
    entry_frame.columnconfigure(1, weight=1)

    create_entry_label(entry_frame, "妮姬", row=0, column=0)
    name_var = tk.StringVar(value=str(row.get("name", "") or "").strip())
    name_entry = create_entry(entry_frame, name_var, row=0, column=1)
    name_entry.configure(state="readonly")

    create_entry_label(entry_frame, "Type", row=1, column=0)
    type_var = tk.StringVar(value=str(row.get("type", "") or "").strip())
    create_combobox(entry_frame, type_var, TYPE_VALUES, row=1, column=1)

    btn_frame = create_btn_frame(frame, row=1, column=0)
    btn_frame.columnconfigure(0, weight=1)
    btn_frame.columnconfigure(1, weight=1)

    msg_var = tk.StringVar(value="")
    msg_label = create_msg_label(frame, msg_var, row=2, column=0, sticky="w")
    msg_label.configure(foreground="red", anchor="w", justify="left")

    result: Dict[str, Any] = {"ok": False, "message": ""}

    def _on_save() -> None:
        new_type = type_var.get().strip()
        if new_type not in TYPE_VALUES:
            msg_var.set("請先選擇 type")
            return

        try:
            target_name_code = int(row.get("name_code"))
        except Exception:
            msg_var.set("name_code 無效")
            return

        old_type = str(row.get("type", "") or "").strip()
        if new_type == old_type:
            result["ok"] = True
            result["message"] = "健檢妮姬類型未變更"
            close_dialog(dlg)
            return

        items = load_healthcheck_members()
        existing_keys = {(int(item.get("name_code")), str(item.get("type"))) for item in items}
        if (target_name_code, new_type) in existing_keys:
            msg_var.set("相同妮姬與 type 已存在")
            return

        updated = False
        for item in items:
            try:
                item_name_code = int(item.get("name_code"))
            except Exception:
                continue
            item_type = str(item.get("type", "") or "").strip()
            if item_name_code == target_name_code and item_type == old_type:
                item["type"] = new_type
                updated = True
                break

        if not updated:
            msg_var.set("找不到要編輯的健檢妮姬")
            return

        save_healthcheck_members(items)
        result["ok"] = True
        result["message"] = "已更新健檢妮姬類型"
        close_dialog(dlg)

    create_btn_between(btn_frame, "儲存", _on_save, row=0, column=0, sticky="we")
    create_btn_last(btn_frame, "取消", lambda: close_dialog(dlg), row=0, column=1, sticky="we")

    dlg.protocol("WM_DELETE_WINDOW", lambda: close_dialog(dlg))
    dlg.wait_window(dlg)
    return result if result["ok"] else None
