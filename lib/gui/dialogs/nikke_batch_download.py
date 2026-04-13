# -*- coding: utf-8 -*-
"""Trigger whole-union Nikke batch download from the GUI."""

from __future__ import annotations

import functools
import threading

from ...jobs.nikke_batch_download import run_nikke_batch_download
from ...log import log_exc


def open_batch_download_nikke(app) -> None:
    """Start whole-union Nikke batch download immediately."""
    if not getattr(app, "_last_slug", None):
        app._set_status("請先登入", "red")
        return

    app.nikke_status_var.set("下載中...")
    app.nikke_status_label.configure(foreground="green")
    app.batch_download_nikke_btn.config(state="disabled")
    threading.Thread(target=_batch_download_nikke_worker, args=(app,), daemon=True).start()


def _batch_download_nikke_worker(app) -> None:
    ok = False
    ok_count = 0
    total_count = 0
    msg = ""
    try:
        result = run_nikke_batch_download(account=app._last_slug or "")
        ok = result.ok
        ok_count = int(result.stats.get("ok_count", 0) or 0)
        total_count = int(result.stats.get("total_count", 0) or 0)
        msg = result.message
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        msg = str(exc)

    app.root.after(0, functools.partial(_finish_batch_download, app, ok, ok_count, total_count, msg))


def _finish_batch_download(app, ok: bool, ok_count: int, total_count: int, msg: str) -> None:
    app.batch_download_nikke_btn.config(state="normal")
    if not ok:
        app.nikke_status_var.set(f"下載失敗：{msg or '未知錯誤'}")
        app.nikke_status_label.configure(foreground="red")
        return

    app.nikke_status_var.set(f"下載成功 ({ok_count}/{total_count})")
    app.nikke_status_label.configure(foreground="green")
