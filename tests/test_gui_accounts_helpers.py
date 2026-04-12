# -*- coding: utf-8 -*-
"""針對 accounts 對話框的輔助函式進行最小化測試（不進入 mainloop）。"""
import tkinter as tk

from lib.gui.dialogs import accounts


def test_refresh_list_and_get_selected_index():
    root = tk.Tk()
    root.withdraw()
    try:
        lb = tk.Listbox(root, exportselection=False)
        data = [
            {"nickname": "N1", "username": "u1"},
            {"nickname": "N2", "username": "u2"},
        ]
        accounts._refresh_list(lb, data)
        # Listbox 內容應與輸入筆數一致，且每行前面有空白
        assert lb.size() == 2
        assert lb.get(0).startswith(" ")
        # 未選擇時應為 None
        assert accounts._get_selected_index(lb) is None
        # 選擇第一筆後應回傳索引 0
        lb.selection_set(0)
        assert accounts._get_selected_index(lb) == 0
    finally:
        root.destroy()


def test_delete_accounts_updates_list_and_msg():
    root = tk.Tk()
    root.withdraw()
    try:
        lb = tk.Listbox(root, exportselection=False)
        msg_var = tk.StringVar()
        msg_label = tk.Label(root, textvariable=msg_var)
        data = [{"nickname": "N1", "username": "u1"}, {"nickname": "N2", "username": "u2"}]
        accounts._refresh_list(lb, data)
        # 未選擇時應提示
        accounts._delete_accounts(lb, data, msg_var, msg_label)
        assert msg_var.get() == "請先選擇要刪除的帳號"
        # 選擇後刪除
        lb.selection_set(0)
        accounts._delete_accounts(lb, data, msg_var, msg_label)
        assert lb.size() == 1
        assert msg_var.get() == "已刪除帳號"
    finally:
        root.destroy()
