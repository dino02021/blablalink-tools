# -*- coding: utf-8 -*-
"""Headless Nikke batch download job."""

from __future__ import annotations

import functools
from typing import Any, Dict, List, Tuple

from ..api import (
    build_character_details_path,
    fetch_guild_members,
    fetch_user_character_details,
    fetch_user_characters,
)
from ..constants import CHAR_DETAILS_DIR
from ..data_store import prepare_checklist_items
from ..log import log_exc
from ..utils import load_session, run_with_session_context
from .common import JobResult, resolve_account, run_coro_sync


def _filter_checklist_items(
    checklist_items: List[Tuple[int, str]],
    name_codes: List[Any],
) -> List[Tuple[int, str]]:
    try:
        code_set = {int(value) for value in name_codes}
    except Exception:
        code_set = set()
        for value in name_codes:
            try:
                code_set.add(int(value))
            except Exception:
                continue
    if not code_set:
        return []
    return [item for item in checklist_items if item[0] in code_set]


async def _batch_download_with_ctx(ctx, checklist_items: List[Tuple[int, str]]) -> Dict[str, Any]:
    guild_id, members = await fetch_guild_members(ctx)
    if not guild_id:
        return {"guild_id": "", "files": [], "ok_count": 0, "total_count": 0}

    created_files: List[str] = []
    ok_count = 0
    total_count = 0
    for member in members:
        if not isinstance(member, dict):
            continue
        nickname = str(member.get("nickname", "") or "").strip()
        member_id = str(member.get("member_id", "") or "").strip()
        if not nickname or not member_id:
            continue

        total_count += 1
        name_codes = await fetch_user_characters(ctx, intl_open_id=member_id)
        if not name_codes:
            continue
        filtered = _filter_checklist_items(checklist_items, name_codes)
        if not filtered:
            continue

        before_exists = build_character_details_path(nickname).exists()
        ok = await fetch_user_character_details(ctx, filtered, nickname, intl_open_id=member_id)
        if not ok:
            continue

        ok_count += 1
        outfile = build_character_details_path(nickname)
        if outfile.exists():
            created_files.append(outfile.name)
        elif before_exists:
            created_files.append(outfile.name)

    return {
        "guild_id": guild_id,
        "files": created_files,
        "ok_count": ok_count,
        "total_count": total_count,
    }


def run_nikke_batch_download(account: str | None = None) -> JobResult:
    slug = resolve_account(account)
    if not slug:
        return JobResult(ok=False, message="缺少帳號")

    name_ok, checklist_items, prep_msg = prepare_checklist_items()
    if not name_ok:
        return JobResult(ok=False, message=prep_msg or "無法準備妮姬檢查清單")

    state = load_session(slug)
    if not state:
        return JobResult(ok=False, message="找不到登入 Session")

    try:
        result = run_coro_sync(
            functools.partial(
                run_with_session_context,
                state,
                functools.partial(_batch_download_with_ctx, checklist_items=checklist_items),
            )
        )
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return JobResult(ok=False, message=str(exc))

    guild_id = str(result.get("guild_id", "") or "").strip()
    files = [str(item).strip() for item in result.get("files", []) if str(item).strip()]
    ok_count = int(result.get("ok_count", 0) or 0)
    total_count = int(result.get("total_count", 0) or 0)

    if not guild_id:
        return JobResult(
            ok=False,
            message="找不到聯盟資訊",
            stats={"account": slug, "ok_count": ok_count, "total_count": total_count},
            outputs={"char_details_dir": str(CHAR_DETAILS_DIR), "files": files},
        )

    if total_count <= 0:
        return JobResult(
            ok=False,
            message="聯盟成員清單為空",
            stats={"account": slug, "guild_id": guild_id, "ok_count": ok_count, "total_count": total_count},
            outputs={"char_details_dir": str(CHAR_DETAILS_DIR), "guild_id": guild_id, "files": files},
        )

    if ok_count <= 0:
        return JobResult(
            ok=False,
            message="批次下載妮姬資料失敗",
            stats={"account": slug, "guild_id": guild_id, "ok_count": ok_count, "total_count": total_count},
            outputs={"char_details_dir": str(CHAR_DETAILS_DIR), "guild_id": guild_id, "files": files},
        )

    return JobResult(
        ok=True,
        message=f"批次下載妮姬資料成功 ({ok_count}/{total_count})",
        stats={"account": slug, "guild_id": guild_id, "ok_count": ok_count, "total_count": total_count},
        outputs={"char_details_dir": str(CHAR_DETAILS_DIR), "guild_id": guild_id, "files": files},
    )
