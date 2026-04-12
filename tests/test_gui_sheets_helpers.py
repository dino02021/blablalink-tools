# -*- coding: utf-8 -*-
"""針對 sheets 對話框的輔助函式做最小化測試。"""
import tkinter as tk

from lib.gui.dialogs import sheets


def test_refresh_sheets_and_types_and_get_selected():
    try:
        root = tk.Tk()
    except tk.TclError:
        import pytest

        pytest.skip("Tk 無法啟動，跳過 GUI 測試")
        return
    root.withdraw()
    try:
        lb_sheets = tk.Listbox(root, exportselection=False)
        data = [{"type": "T1", "sheet_name": "S1"}, {"type": "T2", "sheet_name": "S2"}]
        sheets._refresh_sheets(lb_sheets, data)
        assert lb_sheets.size() == 2
        assert lb_sheets.get(0).startswith(" ")
        assert sheets._get_selected_index(lb_sheets) is None
        lb_sheets.selection_set(1)
        assert sheets._get_selected_index(lb_sheets) == 1

        lb_types = tk.Listbox(root, exportselection=False)
        types = ["A", "B"]
        sheets._refresh_types(lb_types, types)
        assert lb_types.size() == 2
        assert sheets._get_selected_type(lb_types) is None
        lb_types.selection_set(0)
        assert sheets._get_selected_type(lb_types) == 0
    finally:
        root.destroy()
