# -*- coding: utf-8 -*-
"""Headless union raid download job."""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from ..api import (
    fetch_union_info,
    fetch_union_raid_data_detail,
    fetch_union_raid_data_of_guild_season_detail,
)
from ..constants import UNION_RAID_TEMP_FILE
from ..log import log_exc
from ..utils import load_session, run_with_session_context
from .common import JobResult, resolve_account, run_coro_sync


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


async def _download_union_detail_async(ctx, season_id: str | None = None) -> Tuple[bool, Dict[str, Any]]:
    union_id, intl_open_id, nikke_area_id = await fetch_union_info(ctx)
    detail: Dict[str, Any] = {
        "union_id": str(union_id or "").strip(),
        "nikke_area_id": str(nikke_area_id or "").strip(),
        "season_id": str(season_id or "").strip(),
    }
    target_season_id = str(season_id or "").strip()
    if target_season_id:
        if not (union_id and nikke_area_id):
            missing = []
            if not union_id:
                missing.append("union_id")
            if not nikke_area_id:
                missing.append("nikke_area_id")
            detail["message"] = f"缺少聯盟資訊: {', '.join(missing)}"
            return False, detail
        ok, message = await fetch_union_raid_data_of_guild_season_detail(ctx, nikke_area_id, union_id, target_season_id)
        if message:
            detail["message"] = message
        return ok, detail
    if not (union_id and intl_open_id and nikke_area_id):
        missing = []
        if not union_id:
            missing.append("union_id")
        if not intl_open_id:
            missing.append("intl_open_id")
        if not nikke_area_id:
            missing.append("nikke_area_id")
        detail["message"] = f"缺少聯盟資訊: {', '.join(missing)}"
        return False, detail
    ok, message = await fetch_union_raid_data_detail(ctx, union_id, intl_open_id, nikke_area_id)
    if message:
        detail["message"] = message
    return ok, detail


async def _download_union_async(ctx, season_id: str | None = None) -> bool:
    ok, _detail = await _download_union_detail_async(ctx, season_id=season_id)
    return ok


def run_union_raid_download(account: str | None = None, season_id: str | None = None) -> JobResult:
    slug = resolve_account(account)
    if not slug:
        return JobResult(ok=False, message="找不到指定帳號")

    state = load_session(slug)
    if not state:
        return JobResult(ok=False, message="找不到 Session")

    before_path = _find_latest_union_raid_file()
    try:
        download_result = run_coro_sync(
            functools.partial(
                run_with_session_context,
                state,
                functools.partial(_download_union_detail_async, season_id=season_id),
            )
        )
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return JobResult(ok=False, message=str(exc))

    if isinstance(download_result, tuple) and len(download_result) == 2:
        ok = bool(download_result[0])
        detail = download_result[1] if isinstance(download_result[1], dict) else {}
    else:
        ok = bool(download_result)
        detail = {}

    if not ok:
        message = str(detail.get("message", "") or "").strip() or "下載聯盟突襲資料失敗"
        return JobResult(ok=False, message=f"下載聯盟突襲資料失敗: {message}", outputs=detail)

    latest_path = _find_latest_union_raid_file()
    outputs = dict(detail)
    if latest_path:
        outputs["data_path"] = str(latest_path)
        outputs["created_new_file"] = str(latest_path) != str(before_path) if before_path else True

    return JobResult(
        ok=True,
        message="下載聯盟突襲資料成功",
        stats={"account": slug, "season_id": str(season_id or "").strip()},
        outputs=outputs,
    )
