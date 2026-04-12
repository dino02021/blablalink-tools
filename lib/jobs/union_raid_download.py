# -*- coding: utf-8 -*-
"""Headless union raid download job."""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Optional

from ..api import fetch_union_info, fetch_union_raid_data
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


async def _download_union_async(ctx) -> bool:
    union_id, intl_open_id, nikke_area_id = await fetch_union_info(ctx)
    if not (union_id and intl_open_id and nikke_area_id):
        return False
    return await fetch_union_raid_data(ctx, union_id, intl_open_id, nikke_area_id)


def run_union_raid_download(account: str | None = None) -> JobResult:
    slug = resolve_account(account)
    if not slug:
        return JobResult(ok=False, message="找不到下載帳號")

    state = load_session(slug)
    if not state:
        return JobResult(ok=False, message="找不到登入 Session")

    before_path = _find_latest_union_raid_file()
    try:
        ok = run_coro_sync(functools.partial(run_with_session_context, state, _download_union_async))
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return JobResult(ok=False, message=str(exc))

    if not ok:
        return JobResult(ok=False, message="下載聯盟突襲資料失敗")

    latest_path = _find_latest_union_raid_file()
    outputs = {}
    if latest_path:
        outputs["data_path"] = str(latest_path)
        outputs["created_new_file"] = str(latest_path) != str(before_path) if before_path else True

    return JobResult(
        ok=True,
        message="下載聯盟突襲資料成功",
        stats={"account": slug},
        outputs=outputs,
    )
