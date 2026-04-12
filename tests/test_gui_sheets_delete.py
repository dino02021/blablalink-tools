# -*- coding: utf-8 -*-
"""針對 sheets 對話框的刪除行為測試。"""
import tkinter as tk

from lib.gui.dialogs import sheets


def test_delete_sheet_and_type_msgs():
    root = tk.Tk()
    root.withdraw()
    try:
        msg_var = tk.StringVar()
        msg_label = tk.Label(root, textvariable=msg_var)

        # 刪除試算表
        lb_sheet = tk.Listbox(root, exportselection=False)
        data = [{"type": "T1", "sheet_name": "S1"}, {"type": "T2", "sheet_name": "S2"}]
        sheets._refresh_sheets(lb_sheet, data)

        # 未選擇時提示
        sheets._delete_sheet(lb_sheet, data, msg_var, msg_label)
        assert msg_var.get() == "請先選擇試算表"
        # 選擇後刪除
        lb_sheet.selection_set(0)
        sheets._delete_sheet(lb_sheet, data, msg_var, msg_label)
        assert msg_var.get() == "已刪除試算表"
        assert lb_sheet.size() == 1

        # 刪除類型
        msg_var.set("")
        lb_type = tk.Listbox(root, exportselection=False)
        types = ["A", "B"]
        sheets._refresh_types(lb_type, types)
        sheets._delete_type(lb_type, types, msg_var, msg_label)
        assert msg_var.get() == "請先選擇類型"
        lb_type.selection_set(1)
        sheets._delete_type(lb_type, types, msg_var, msg_label)
        assert msg_var.get() == "已刪除類型"
        assert lb_type.size() == 1
    finally:
        root.destroy()
