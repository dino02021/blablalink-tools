# -*- coding: utf-8 -*-
"""Healthcheck management entry dialog."""

from __future__ import annotations

import tkinter as tk
from typing import Any, Dict, Optional

from ..layout import (
    close_dialog,
    create_btn_between,
    create_btn_frame,
    create_dialog,
    create_frame,
    create_msg_label,
)
from .healthcheck_members import open_healthcheck_members_dialog


def open_healthcheck_dialog(app: Any) -> Optional[Dict[str, Any]]:
    """Open healthcheck management entry dialog."""
    parent = app.root if hasattr(app, "root") else app
    dlg = create_dialog(parent, "健檢管理", 320, 120)

    frame = create_frame(dlg)
    frame.columnconfigure(0, weight=1)

    btn_frame = create_btn_frame(frame, row=0, column=0)
    btn_frame.columnconfigure(0, weight=1)

    msg_var = tk.StringVar(value="")
    msg_label = create_msg_label(frame, msg_var, row=1, column=0, sticky="w")
    msg_label.configure(foreground="green", anchor="w", justify="left")

    result: Dict[str, Any] = {"ok": False, "message": ""}

    def _open_members() -> None:
        sub_result = open_healthcheck_members_dialog(app, dlg)
        if not sub_result:
            return
        result["ok"] = True
        result["message"] = str(sub_result.get("message", "健檢妮姬名單已更新"))
        msg_var.set(result["message"])

    create_btn_between(btn_frame, "健檢妮姬名單", _open_members, row=0, column=0, sticky="we")

    dlg.protocol("WM_DELETE_WINDOW", lambda: close_dialog(dlg))
    dlg.wait_window(dlg)
    return result if result["ok"] else None
