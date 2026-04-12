# -*- coding: utf-8 -*-
"""GUI wrapper for union raid download."""

from __future__ import annotations

import functools
import threading

from ..jobs.union_raid_download import run_union_raid_download
from ..log import log_exc


def start_download_union(app) -> None:
    """Start union raid download from the GUI."""
    if not getattr(app, "_last_slug", None):
        app._set_status("請先登入", "red")
        return

    app._set_status("下載聯盟突襲資料中...", "green")
    app.union_status_var.set("")
    app.download_union_btn.config(state="disabled")
    threading.Thread(target=_download_union_worker, args=(app,), daemon=True).start()


def _download_union_worker(app) -> None:
    ok = False
    msg = ""
    try:
        result = run_union_raid_download(account=app._last_slug or "")
        ok = result.ok
        msg = result.message
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        msg = str(exc)

    app.root.after(0, functools.partial(_finish_download_union, app, ok, msg))


def _finish_download_union(app, ok: bool, msg: str) -> None:
    app.download_union_btn.config(state="normal")
    if ok:
        app._set_status("下載聯盟突襲資料成功", "green")
        app.union_status_var.set("下載聯盟突襲資料成功")
        app.union_status_label.configure(foreground="green")
        return
    app._set_status(msg or "下載聯盟突襲資料失敗", "red")
    app.union_status_var.set(msg or "下載聯盟突襲資料失敗")
    app.union_status_label.configure(foreground="red")
