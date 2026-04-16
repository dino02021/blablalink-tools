# -*- coding: utf-8 -*-
"""聯盟突襲傷害表更新對話框：單一/批次模式選擇 + 月份輸入。"""

from __future__ import annotations

import tkinter as tk
from datetime import datetime
from tkinter import ttk
from typing import Any, Dict, Optional

from ..layout import (
    close_dialog,
    create_dialog,
    create_frame,
    create_entry_label,
    create_entry,
    create_entry_frame,
    create_btn_frame,
    create_btn_between,
    create_btn_last,
    create_msg_label,
)



def _get_default_month(date_str: str | None = None) -> str:
    """取得預設月份 (YYYY/MM 格式)。"""
    try:
        if date_str:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
        else:
            dt = datetime.now()
        return dt.strftime("%Y/%m")
    except (ValueError, TypeError):
        return datetime.now().strftime("%Y/%m")


def open_union_raid_update_dialog(parent: tk.Misc) -> Optional[Dict[str, Any]]:
    """開啟聯盟突襲傷害表更新對話框。"""
    dlg = create_dialog(parent, "更新聯盟突襲傷害表", 320, 140)

    frame = create_frame(dlg)
    frame.columnconfigure(0, weight=1)

    # 月份輸入框
    entry_frame = create_entry_frame(frame, row=0, column=0)
    entry_frame.columnconfigure(1, weight=1)
    
    current_month = _get_default_month()
    create_entry_label(entry_frame, "上傳月份 (YYYY/MM):", row=0, column=0)
    month_var = tk.StringVar(value=current_month)
    create_entry(entry_frame, month_var, row=0, column=1, sticky="ew")

    # 按鈕框架
    btn_frame = create_btn_frame(frame, row=1, column=0)
    btn_frame.columnconfigure(0, weight=1)
    btn_frame.columnconfigure(1, weight=1)

    status_var = tk.StringVar(value="")
    status_label = create_msg_label(frame, status_var, row=2, column=0, sticky="w")

    result: Dict[str, Any] = {"ok": False, "message": "", "mode": None, "month": current_month}

    def _handle_single() -> None:
        result["ok"] = True
        result["mode"] = "single"
        result["month"] = month_var.get().strip()
        result["message"] = "選擇單一帳號"
        close_dialog(dlg)

    def _handle_batch() -> None:
        result["ok"] = True
        result["mode"] = "all"
        result["month"] = month_var.get().strip()
        result["message"] = "選擇批次帳號"
        close_dialog(dlg)

    create_btn_between(btn_frame, "單一帳號", _handle_single, row=0, column=0, sticky="we")
    create_btn_last(btn_frame, "批次帳號", _handle_batch, row=0, column=1, sticky="we")

    dlg.protocol("WM_DELETE_WINDOW", lambda: close_dialog(dlg))
    dlg.wait_window(dlg)

    return result if result["ok"] else None
