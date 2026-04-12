# -*- coding: utf-8 -*-
"""Headless Nikke batch download job."""

from __future__ import annotations

import functools
from typing import Dict, List, Tuple

from ..api import fetch_user_character_details, fetch_user_characters
from ..constants import CHAR_DETAILS_DIR
from ..data_store import load_members, prepare_checklist_items
from ..log import log_exc
from ..utils import load_session, run_with_session_context
from .common import JobResult, resolve_account, run_coro_sync


def _filter_checklist_items(
    checklist_items: List[Tuple[int, str]],
    name_codes: List,
) -> List[Tuple[int, str]]:
    try:
        code_set = {int(x) for x in name_codes}
    except Exception:
        code_set = set()
        for x in name_codes:
            try:
                code_set.add(int(x))
            except Exception:
                continue
    if not code_set:
        return []
    return [item for item in checklist_items if item[0] in code_set]


async def _batch_download_with_ctx(ctx, members, checklist_items) -> int:
    ok_count = 0
    for mem in members:
        nickname = mem.get("nick_name")
        intl_id = mem.get("intl_open_id")
        if not nickname or not intl_id:
            continue
        name_codes = await fetch_user_characters(ctx, intl_open_id=intl_id)
        if not name_codes:
            continue
        filtered = _filter_checklist_items(checklist_items, name_codes)
        if not filtered:
            continue
        res = await fetch_user_character_details(ctx, filtered, nickname, intl_open_id=intl_id)
        if res:
            ok_count += 1
    return ok_count


def run_nikke_batch_download(
    account: str | None = None,
    members: List[Dict[str, str]] | None = None,
) -> JobResult:
    slug = resolve_account(account)
    if not slug:
        return JobResult(ok=False, message="找不到下載帳號")

    name_ok, checklist_items, prep_msg = prepare_checklist_items()
    if not name_ok:
        return JobResult(ok=False, message=prep_msg or "缺少妮姬清單資料")

    target_members = list(members or load_members())
    if not target_members:
        return JobResult(ok=False, message="沒有可下載的成員資料")

    state = load_session(slug)
    if not state:
        return JobResult(ok=False, message="找不到登入 Session")

    try:
        ok_count = run_coro_sync(
            functools.partial(
                run_with_session_context,
                state,
                functools.partial(_batch_download_with_ctx, members=target_members, checklist_items=checklist_items),
            )
        )
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return JobResult(ok=False, message=str(exc))

    total_count = len(target_members)
    if ok_count <= 0:
        return JobResult(
            ok=False,
            message="批次下載妮姬資料失敗",
            stats={"account": slug, "ok_count": ok_count, "total_count": total_count},
            outputs={"char_details_dir": str(CHAR_DETAILS_DIR)},
        )

    return JobResult(
        ok=True,
        message=f"批次下載妮姬資料成功 ({ok_count}/{total_count})",
        stats={"account": slug, "ok_count": ok_count, "total_count": total_count},
        outputs={"char_details_dir": str(CHAR_DETAILS_DIR)},
    )
