# -*- coding: utf-8 -*-
"""Headless Nikke batch download job."""

from __future__ import annotations

import functools
from typing import Any, Dict, List, Tuple

from ..api import (
    build_character_details_path,
    fetch_union_info,
    fetch_guild_members,
    fetch_user_character_details,
    fetch_user_characters,
)
from ..constants import CHAR_DETAILS_DIR
from ..data_store import build_healthcheck_function_id_targets, prepare_checklist_items
from ..log import log_exc
from ..utils import load_session, run_with_session_context
from .common import JobResult, resolve_account, run_coro_sync


def _filter_checklist_items(
    checklist_items: List[Tuple[int, str]],
    name_codes: List[Any],
) -> List[Tuple[int, str]]:
    code_set: set[int] = set()
    for name_code in name_codes:
        try:
            code_set.add(int(name_code))
        except Exception:
            continue
    if not code_set:
        return []
    return [item for item in checklist_items if item[0] in code_set]


def _to_int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _build_user_character_lookup(user_characters: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    lookup: Dict[str, Dict[str, Any]] = {}
    for item in user_characters:
        if not isinstance(item, dict):
            continue
        name_code = item.get("name_code")
        if name_code is None:
            continue
        grade = item.get("grade")
        core = item.get("core")
        lookup[str(name_code)] = {
            "grade": grade,
            "core": core,
            "breakthrough": _to_int_or_zero(grade) + _to_int_or_zero(core),
        }
    return lookup


async def _batch_download_with_ctx(
    ctx,
    checklist_items: List[Tuple[int, str]],
    function_id_targets: Dict[str, List[int | None]],
) -> Dict[str, Any]:
    guild_id, _fetched_open_id, nikke_area_id = await fetch_union_info(ctx)
    target_guild_id = str(guild_id or "").strip()
    target_nikke_area_id = str(nikke_area_id or "").strip()
    if not target_guild_id or not target_nikke_area_id:
        return {"guild_id": "", "files": [], "ok_count": 0, "total_count": 0}
    guild_id, members = await fetch_guild_members(
        ctx,
        guild_id=target_guild_id,
        nikke_area_id=target_nikke_area_id,
    )
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
        user_characters = await fetch_user_characters(
            ctx,
            intl_open_id=member_id,
            nikke_area_id=target_nikke_area_id,
        )
        if not user_characters:
            continue
        name_codes = [item.get("name_code") for item in user_characters if isinstance(item, dict)]
        filtered = _filter_checklist_items(checklist_items, name_codes)
        if not filtered:
            continue
        user_character_lookup = _build_user_character_lookup(user_characters)

        before_exists = build_character_details_path(nickname).exists()
        ok = await fetch_user_character_details(
            ctx,
            filtered,
            nickname,
            intl_open_id=member_id,
            nikke_area_id=target_nikke_area_id,
            function_id_targets=function_id_targets,
            user_character_lookup=user_character_lookup,
        )
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


def run_nikke_batch_download(
    account: str | None = None,
    function_id_targets: Dict[str, List[int | None]] | None = None,
) -> JobResult:
    slug = resolve_account(account)
    if not slug:
        return JobResult(ok=False, message="缺少帳號")

    name_ok, checklist_items, prep_msg = prepare_checklist_items()
    if not name_ok:
        return JobResult(ok=False, message=prep_msg or "無法準備妮姬檢查清單")

    if function_id_targets is None:
        function_id_targets = build_healthcheck_function_id_targets()

    state = load_session(slug)
    if not state:
        return JobResult(ok=False, message="找不到登入 Session")

    try:
        result = run_coro_sync(
            functools.partial(
                run_with_session_context,
                state,
                functools.partial(
                    _batch_download_with_ctx,
                    checklist_items=checklist_items,
                    function_id_targets=function_id_targets,
                ),
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
