# -*- coding: utf-8 -*-
"""聯盟突襲資料下載：包含下載流程與 UI 狀態更新。"""

from __future__ import annotations

import functools
import threading

from ..api import fetch_union_info, fetch_union_raid_data
from ..log import log_exc
from ..utils import load_session, run_with_session_context


def start_download_union(app) -> None:
    """由主畫面呼叫：檢查登入並啟動下載流程。"""
    if not getattr(app, "_last_slug", None):
        app._set_status("請先登入", "red")
        return

    app._set_status("下載聯盟突襲資料中...", "green")
    app.union_status_var.set("")
    app.download_union_btn.config(state="disabled")
    threading.Thread(target=_download_union_worker, args=(app,), daemon=True).start()


def _download_union_worker(app) -> None:
    """背景下載流程：使用 session 呼叫聯盟突襲 API。"""
    ok = False
    msg = ""
    try:
        state = load_session(app._last_slug or "")
        if not state:
            msg = "找不到登入資訊"
        else:
            coro_factory = functools.partial(run_with_session_context, state, _download_union_async)
            ok = app._run_coro_sync(coro_factory)
            if not ok:
                msg = "下載失敗"
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        msg = str(exc)

    app.root.after(0, functools.partial(_finish_download_union, app, ok, msg))


async def _download_union_async(ctx) -> bool:
    """使用已建立的 browser context 取得聯盟突襲資料。"""
    union_id, intl_open_id, nikke_area_id = await fetch_union_info(ctx)
    if not (union_id and intl_open_id and nikke_area_id):
        return False
    return await fetch_union_raid_data(ctx, union_id, intl_open_id, nikke_area_id)


def _finish_download_union(app, ok: bool, msg: str) -> None:
    """主執行緒更新 UI。"""
    app.download_union_btn.config(state="normal")
    if ok:
        app._set_status("下載完成", "green")
        app.union_status_var.set("下載完成")
        app.union_status_label.configure(foreground="green")
        return
    app._set_status(msg or "下載失敗", "red")
    app.union_status_var.set(msg or "下載失敗")
    app.union_status_label.configure(foreground="red")
