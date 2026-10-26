# -*- coding: utf-8 -*-
"""GUI wrapper for nikke update (download + upload)."""

from __future__ import annotations

import functools
import threading
from typing import TYPE_CHECKING

from ..data_store import load_union_account_map
from ..healthcheck_config import load_healthcheck_snapshot
from ..jobs.nikke_batch_download import run_nikke_batch_download
from ..jobs.nikke_upload import run_nikke_upload, resolve_character_sheet_target
from ..log import log_exc

if TYPE_CHECKING:
    from . import gui_main


def start_nikke_update(app: gui_main.App, mode: str) -> None:
    """Start nikke update (download + upload) from the GUI."""
    if mode not in ("single", "all"):
        app._set_nikke_status("模式錯誤", "red")
        return

    app._set_nikke_status("正在更新練度資料表...", "green")
    app.nikke_status_label.configure(foreground="green")
    app._set_busy(True)
    
    threading.Thread(target=_update_worker, args=(app, mode), daemon=True).start()


def _update_worker(app, mode: str) -> None:
    """背景執行緒：下載 + 上傳妮姬資料。"""
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
    snapshot = load_healthcheck_snapshot()
    download_result = run_nikke_batch_download(account=slug, healthcheck_snapshot=snapshot)
    if not download_result.ok:
        is_skipped = bool(download_result.outputs.get("skip_reason"))
        app.root.after(0, functools.partial(_finish_update, app, is_skipped, download_result.message))
        return

    # 取 guild_id
    guild_id = str(download_result.outputs.get("guild_id", "") or "").strip()
    files = [
        str(item).strip()
        for item in (download_result.outputs.get("files", []) or [])
        if str(item).strip()
    ]
    
    if not guild_id:
        msg = "無下載資料"
        app.root.after(0, functools.partial(_finish_update, app, False, msg))
        return

    # 查試算表
    try:
        target_sheet_id, target_sheet_name = resolve_character_sheet_target(guild_id)
    except ValueError as exc:
        app.root.after(0, functools.partial(_finish_update, app, False, str(exc)))
        return

    # 上傳
    upload_result = run_nikke_upload(account=slug, files=files, sheet_id=target_sheet_id, healthcheck_snapshot=snapshot,
                                     expected_members=download_result.outputs.get("expected_members"))
    ok = upload_result.ok
    msg = upload_result.message
    skipped_count = int(download_result.stats.get("skipped_count", 0) or 0)
    if skipped_count:
        msg += f"；正常略過 {skipped_count} 名未持有檢查角色的成員（共 {download_result.stats.get('total_count', 0)} 人）"
    
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
    snapshot = load_healthcheck_snapshot()

    for idx, item in enumerate(account_map):
        username = str(item.get("username", "")).strip()
        if not username:
            continue

        # 更新狀態
        status_msg = f"處理帳號 {idx + 1}/{len(account_map)}: {username}"
        app.root.after(0, lambda msg=status_msg: (
            app._set_nikke_status(msg, "green"),
            app.nikke_status_var.set(msg)
        ))

        # 下載
        download_result = run_nikke_batch_download(account=username, healthcheck_snapshot=snapshot)
        if not download_result.ok:
            if download_result.outputs.get("skip_reason"):
                messages.append(f"⊘ {username}: {download_result.message}")
                continue
            messages.append(f"❌ {username}: 下載失敗 - {download_result.message}")
            all_ok = False
            continue

        # 取 guild_id
        guild_id = str(download_result.outputs.get("guild_id", "") or "").strip()
        files = [
            str(item).strip()
            for item in (download_result.outputs.get("files", []) or [])
            if str(item).strip()
        ]
        
        if not guild_id:
            messages.append(f"❌ {username}: 無下載資料")
            all_ok = False
            continue

        # 查試算表
        try:
            target_sheet_id, target_sheet_name = resolve_character_sheet_target(guild_id)
        except ValueError as exc:
            messages.append(f"⊘ {username}: {str(exc)}")
            continue

        # 上傳
        upload_result = run_nikke_upload(account=username, files=files, sheet_id=target_sheet_id, healthcheck_snapshot=snapshot,
                                         expected_members=download_result.outputs.get("expected_members"))
        if upload_result.ok:
            messages.append(f"✅ {username}: {upload_result.message}，下載 {download_result.stats.get('ok_count', 0)}/{download_result.stats.get('total_count', 0)}，正常略過 {download_result.stats.get('skipped_count', 0)}")
        else:
            messages.append(f"❌ {username}: 上傳失敗 - {upload_result.message}")
            all_ok = False

    final_msg = "\n".join(messages) if messages else "完成"
    app.root.after(0, functools.partial(_finish_update, app, all_ok, final_msg))


def _finish_update(app, ok: bool, msg: str) -> None:
    """完成更新，更新 UI 狀態。"""
    app._set_busy(False)
    if ok:
        app._set_nikke_status("練度資料表更新成功", "green")
        app.nikke_status_var.set(msg or "更新成功")
        app.nikke_status_label.configure(foreground="green")
    else:
        app._set_nikke_status(msg or "練度資料表更新失敗", "red")
        app.nikke_status_var.set(msg or "更新失敗")
        app.nikke_status_label.configure(foreground="red")
