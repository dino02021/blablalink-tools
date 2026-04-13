# -*- coding: utf-8 -*-
"""Headless synchro upload job."""

from __future__ import annotations

import functools
from typing import Any, Dict, Optional

from ..api import fetch_guild_members
from ..google_sheets.auth import get_gspread_client
from ..log import log_exc
from ..utils import load_session, run_with_session_context
from .common import JobResult, resolve_account, run_coro_sync
from .nikke_upload import resolve_character_sheet_target


def _parse_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except Exception:
        return None


def run_sync_upload(account: str | None = None, worksheet_name: str = "成員名稱") -> JobResult:
    slug = resolve_account(account)
    if not slug:
        return JobResult(ok=False, message="缺少帳號")

    state = load_session(slug)
    if not state:
        return JobResult(ok=False, message="找不到登入 Session")

    try:
        guild_id, members = run_coro_sync(functools.partial(run_with_session_context, state, fetch_guild_members))
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return JobResult(ok=False, message=str(exc))

    if not guild_id:
        return JobResult(ok=False, message="找不到聯盟資訊")
    if not isinstance(members, list) or not members:
        return JobResult(ok=False, message="找不到聯盟成員資料")

    member_level_map: Dict[str, int] = {}
    for item in members:
        if not isinstance(item, dict):
            continue
        nickname = str(item.get("nickname", "")).strip()
        if not nickname:
            continue
        level = _parse_int(item.get("synchro_level"))
        if level is None:
            continue
        member_level_map[nickname] = level

    total_count = len(member_level_map)
    if total_count == 0:
        return JobResult(ok=False, message="沒有可上傳的同步等級資料")

    try:
        sheet_id, _sheet_name = resolve_character_sheet_target(guild_id)
    except ValueError as exc:
        return JobResult(ok=False, message=str(exc))

    try:
        client = get_gspread_client()
        sheet = client.open_by_key(sheet_id)
        ws = sheet.worksheet(worksheet_name)
        a_values = ws.col_values(1)
        last_row = max(2, len(a_values))
        ws.batch_update(
            [{"range": f"B2:B{last_row}", "values": [[""] for _ in range(last_row - 1)]}],
            value_input_option="RAW",
        )

        updates = []
        matched_count = 0
        for row_idx in range(2, last_row + 1):
            name = str(a_values[row_idx - 1]).strip() if row_idx - 1 < len(a_values) else ""
            if not name:
                continue
            level = member_level_map.get(name)
            if level is None:
                continue
            updates.append({"range": f"B{row_idx}:B{row_idx}", "values": [[level]]})
            matched_count += 1

        if matched_count == 0:
            return JobResult(ok=False, message="試算表內找不到可更新的成員名稱")

        ws.batch_update(updates, value_input_option="RAW")
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return JobResult(ok=False, message=str(exc))

    return JobResult(
        ok=True,
        message=f"上傳同步成功 ({matched_count}/{total_count})",
        stats={"matched_count": matched_count, "total_count": total_count},
        outputs={"sheet_id": sheet_id, "worksheet_name": worksheet_name, "union_id": guild_id},
    )
