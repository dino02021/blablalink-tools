# -*- coding: utf-8 -*-
"""測試基本的 layout 工具函式：create_dialog / place_on_same_screen / close_dialog。"""
import tkinter as tk

from lib.gui import layout


def test_create_dialog_and_close():
    try:
        root = tk.Tk()
    except tk.TclError:
        import pytest

        pytest.skip("Tk 無法啟動，跳過 GUI 測試")
        return
    root.withdraw()
    try:
        dlg = layout.create_dialog(root, "測試", 200, 120)
        # 應設定好 geometry 寬高
        geo = dlg.geometry()
        assert geo.startswith("200x120")
        layout.close_dialog(dlg)
        # 關閉後呼叫 close_dialog 不應丟例外
        layout.close_dialog(dlg)
    finally:
        try:
            root.destroy()
        except Exception:
            pass
