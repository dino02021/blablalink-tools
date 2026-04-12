# -*- coding: utf-8 -*-
"""Headless union raid upload job."""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Optional

from ..api import fetch_guild_members
from ..constants import UNION_RAID_TEMP_FILE, UNION_RAID_TYPE
from ..data_store import load_sheets
from ..log import log_exc
from ..utils import load_session, run_with_session_context
from .common import JobResult, resolve_account, run_coro_sync


def _default_sheet_id() -> str:
    sheets, _ = load_sheets()
    matches = [item for item in sheets if item.get("type") == UNION_RAID_TYPE]
    if not matches:
        return ""
    return str(matches[0].get("sheet_id", "") or "").strip()


def _find_latest_union_raid_file() -> Optional[Path]:
    base = UNION_RAID_TEMP_FILE.parent
    if not base.exists():
        return None
    candidates = sorted(
        base.glob(f"{UNION_RAID_TEMP_FILE.stem}_*{UNION_RAID_TEMP_FILE.suffix}"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def run_union_raid_upload(
    account: str | None = None,
    month: str | None = None,
    data_path: str | Path | None = None,
    sheet_id: str | None = None,
) -> JobResult:
    slug = resolve_account(account)
    if not slug:
        return JobResult(ok=False, message="找不到上傳帳號")

    month_text = str(month or "").strip()
    if not month_text:
        return JobResult(ok=False, message="缺少 month 參數")

    target_sheet_id = str(sheet_id or _default_sheet_id()).strip()
    if not target_sheet_id:
        return JobResult(ok=False, message="找不到 UnionRaid 試算表設定")

    target_data_path = Path(data_path) if data_path else _find_latest_union_raid_file()
    if target_data_path is None or not Path(target_data_path).exists():
        return JobResult(ok=False, message="找不到聯盟突襲資料檔")

    state = load_session(slug)
    if not state:
        return JobResult(ok=False, message="找不到登入 Session")

    try:
        from ..gui.dialogs.union_raid_upload import _load_union_data, _upload_to_sheets
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return JobResult(ok=False, message=f"載入 union upload 核心失敗：{exc}")

    try:
        fetched = run_coro_sync(functools.partial(run_with_session_context, state, fetch_guild_members))
        guild_members = fetched if isinstance(fetched, list) else []
        if not guild_members:
            return JobResult(ok=False, message="未取得公會成員資料")

        data = _load_union_data(Path(target_data_path))
        if not data:
            return JobResult(ok=False, message="聯盟突襲資料檔內容為空")

        sheet_ok, web_ok, web_msg = _upload_to_sheets(target_sheet_id, month_text, data, guild_members)
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return JobResult(ok=False, message=str(exc))

    outputs = {"data_path": str(target_data_path), "sheet_id": target_sheet_id, "month": month_text}
    if not sheet_ok:
        return JobResult(ok=False, message="上傳聯盟突襲資料失敗", stats={"web_ok": web_ok}, outputs=outputs)

    if not web_ok:
        return JobResult(
            ok=True,
            message=f"總表成功 / WebApp失敗：{web_msg}",
            stats={"partial": True, "web_ok": False},
            outputs=outputs,
        )

    return JobResult(
        ok=True,
        message="上傳聯盟突襲資料成功",
        stats={"partial": False, "web_ok": True},
        outputs=outputs,
    )
