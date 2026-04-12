# -*- coding: utf-8 -*-
"""針對 accounts 內部新增/刪除行為做額外測試。"""
import tkinter as tk

from lib.gui.dialogs import accounts


def test_save_new_account_validation(monkeypatch):
    # 準備 root / listbox / vars
    try:
        root = tk.Tk()
    except tk.TclError:
        import pytest

        pytest.skip("Tk 無法啟動，跳過 GUI 測試")
        return
    root.withdraw()
    try:
        lb = tk.Listbox(root, exportselection=False)
        msg_var_parent = tk.StringVar()
        msg_label_parent = tk.Label(root, textvariable=msg_var_parent)
        accounts_list = []

        # 暫存 save_accounts 呼叫狀態
        called = {"cnt": 0}

        def fake_save(acc):
            called["cnt"] += 1
            # 確保傳入的資料與 accounts_list 同步
            assert acc is accounts_list

        monkeypatch.setattr(accounts, "save_accounts", fake_save)

        # 未填寫時會警告
        warn_var = tk.StringVar()
        dlg_add = tk.Toplevel(root)
        accounts._save_new_account(
            tk.StringVar(value=""), tk.StringVar(value=""), tk.StringVar(value=""),
            accounts_list, lb, warn_var, msg_var_parent, msg_label_parent, dlg_add
        )
        assert warn_var.get() == "暱稱、帳號、密碼都要填寫"
        assert called["cnt"] == 0

        # 填寫完整時會寫入 accounts 並刷新 listbox
        dlg_add_ok = tk.Toplevel(root)
        accounts._save_new_account(
            tk.StringVar(value="nick"), tk.StringVar(value="user"), tk.StringVar(value="pwd"),
            accounts_list, lb, warn_var, msg_var_parent, msg_label_parent, dlg_add_ok
        )
        assert called["cnt"] == 1
        assert len(accounts_list) == 1
        assert lb.size() == 1
        assert msg_var_parent.get() == "已新增帳號"
    finally:
        root.destroy()
