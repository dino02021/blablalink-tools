# -*- coding: utf-8 -*-
"""妮姬資料更新對話框：單一/批次模式選擇。"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Dict, Optional

from ..layout import (
    close_dialog,
    create_dialog,
    create_frame,
    create_btn_frame,
    create_btn_between,
    create_btn_last,
    create_msg_label,
)


def open_nikke_update_dialog(parent: tk.Misc) -> Optional[Dict[str, Any]]:
    """開啟妮姬資料更新對話框。"""
    dlg = create_dialog(parent, "更新練度資料表", 280, 70)

    frame = create_frame(dlg)
    frame.columnconfigure(0, weight=1)

    btn_frame = create_btn_frame(frame, row=0, column=0)
    btn_frame.columnconfigure(0, weight=1)
    btn_frame.columnconfigure(1, weight=1)

    status_var = tk.StringVar(value="")
    status_label = create_msg_label(frame, status_var, row=1, column=0, sticky="w")

    result: Dict[str, Any] = {"ok": False, "message": "", "mode": None}

    def _handle_single() -> None:
        result["ok"] = True
        result["mode"] = "single"
        result["message"] = "選擇單一帳號"
        close_dialog(dlg)

    def _handle_batch() -> None:
        result["ok"] = True
        result["mode"] = "all"
        result["message"] = "選擇批次帳號"
        close_dialog(dlg)

    create_btn_between(btn_frame, "單一帳號", _handle_single, row=0, column=0, sticky="we")
    create_btn_last(btn_frame, "批次帳號", _handle_batch, row=0, column=1, sticky="we")

    dlg.wait_window()
    return result if result["ok"] else None
