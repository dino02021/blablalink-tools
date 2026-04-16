# -*- coding: utf-8 -*-
"""GUI wrapper for union raid update (download + upload)."""

from __future__ import annotations

import functools
import threading
from typing import TYPE_CHECKING

from ..data_store import load_union_account_map
from ..jobs.union_raid_download import run_union_raid_download
from ..jobs.union_raid_upload import run_union_raid_upload
from ..log import log_exc

if TYPE_CHECKING:
    from . import gui_main


def start_union_raid_update(app: gui_main.App, mode: str) -> None:
    """Start union raid update (download + upload) from the GUI."""
    if mode not in ("single", "all"):
        app._set_status("模式錯誤", "red")
        return

    app._set_status("正在更新聯盟突襲傷害表...", "green")
    app.union_status_var.set("")
    app.update_union_btn.config(state="disabled")
    
    threading.Thread(target=_update_worker, args=(app, mode), daemon=True).start()


def _update_worker(app, mode: str) -> None:
    """背景執行緒：下載 + 上傳聯盟突襲資料。"""
    try:
        if mode == "single":
            _process_single_account(app)
        elif mode == "all":
            _process_all_accounts(app)
    except Exception as exc:
        log_exc(exc)
        app.root.after(0, functools.partial(_finish_update, app, False, str(exc)))


def _process_single_account(app) -> None:
    """處理單一帳號：下載 + 上傳。"""
    if not getattr(app, "_last_slug", None):
        msg = "請先登入"
        app.root.after(0, functools.partial(_finish_update, app, False, msg))
        return

    slug = app._last_slug
    
    # 下載
    download_result = run_union_raid_download(account=slug)
    if not download_result.ok:
        app.root.after(0, functools.partial(_finish_update, app, False, download_result.message))
        return

    # 上傳
    data_path = download_result.outputs.get("data_path") if download_result.outputs else None
    if not data_path:
        msg = "無下載資料"
        app.root.after(0, functools.partial(_finish_update, app, False, msg))
        return

    from ..gui.dialogs.union_raid_upload import _get_default_month
    from datetime import datetime
    month = _get_default_month(datetime.now().strftime("%Y-%m-%d"))
    
    upload_result = run_union_raid_upload(account=slug, month=month, data_path=data_path)
    ok = upload_result.ok
    msg = upload_result.message
    
    app.root.after(0, functools.partial(_finish_update, app, ok, msg))


def _process_all_accounts(app) -> None:
    """處理批次帳號：循環下載 + 上傳。"""
    account_map = load_union_account_map()
    if not account_map:
        msg = "找不到 union_account_map.json 或為空"
        app.root.after(0, functools.partial(_finish_update, app, False, msg))
        return

    all_ok = True
    messages = []

    for idx, item in enumerate(account_map):
        username = str(item.get("username", "")).strip()
        if not username:
            continue

        # 更新狀態
        status_msg = f"處理帳號 {idx + 1}/{len(account_map)}: {username}"
        app.root.after(0, lambda msg=status_msg: (
            app._set_status(msg, "green"),
            app.union_status_var.set(msg)
        ))

        # 下載
        download_result = run_union_raid_download(account=username)
        if not download_result.ok:
            messages.append(f"❌ {username}: 下載失敗 - {download_result.message}")
            all_ok = False
            continue

        # 上傳
        data_path = download_result.outputs.get("data_path") if download_result.outputs else None
        if not data_path:
            messages.append(f"❌ {username}: 無下載資料")
            all_ok = False
            continue

        from ..gui.dialogs.union_raid_upload import _get_default_month
        from datetime import datetime
        month = _get_default_month(datetime.now().strftime("%Y-%m-%d"))
        
        upload_result = run_union_raid_upload(account=username, month=month, data_path=data_path)
        if upload_result.ok:
            messages.append(f"✅ {username}: 成功")
        else:
            messages.append(f"❌ {username}: 上傳失敗 - {upload_result.message}")
            all_ok = False

    final_msg = "\n".join(messages) if messages else "完成"
    app.root.after(0, functools.partial(_finish_update, app, all_ok, final_msg))


def _finish_update(app, ok: bool, msg: str) -> None:
    """完成更新，更新 UI 狀態。"""
    app.update_union_btn.config(state="normal")
    if ok:
        app._set_status("聯盟突襲傷害表更新成功", "green")
        app.union_status_var.set(msg or "更新成功")
        app.union_status_label.configure(foreground="green")
    else:
        app._set_status(msg or "聯盟突襲傷害表更新失敗", "red")
        app.union_status_var.set(msg or "更新失敗")
        app.union_status_label.configure(foreground="red")
