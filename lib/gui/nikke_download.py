# -*- coding: utf-8 -*-
"""妮姬資料下載：單筆下載流程。"""

from __future__ import annotations

import functools
import threading
from typing import List, Tuple

from ..api import fetch_user_character_details, fetch_user_characters
from ..data_store import prepare_checklist_items
from ..log import log_exc
from ..utils import load_session, run_with_session_context


def start_download_nikke(app) -> None:
    """由主畫面呼叫：下載本帳號妮姬資料。"""
    if not getattr(app, "_last_slug", None):
        app._set_status("請先登入", "red")
        return
    app.nikke_status_var.set("下載妮姬資料中...")
    app.nikke_status_label.configure(foreground="green")
    app.download_nikke_btn.config(state="disabled")
    threading.Thread(target=_download_nikke_worker, args=(app,), daemon=True).start()


def _download_nikke_worker(app) -> None:
    ok = False
    msg = ""
    try:
        ok, msg = _download_nikke_once(app)
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        msg = str(exc)
    app.root.after(0, functools.partial(_finish_download_nikke, app, ok, msg))


def _finish_download_nikke(app, ok: bool, msg: str) -> None:
    app.download_nikke_btn.config(state="normal")
    if ok:
        app.nikke_status_var.set("下載完成")
        app.nikke_status_label.configure(foreground="green")
        return
    detail = f"：{msg}" if msg else ""
    app.nikke_status_var.set(f"下載失敗{detail}")
    app.nikke_status_label.configure(foreground="red")


def _download_nikke_once(app) -> Tuple[bool, str]:
    name_ok, checklist_items, prep_msg = prepare_checklist_items()
    if not name_ok:
        return False, prep_msg
    state = load_session(app._last_slug or "")
    if not state:
        return False, "找不到登入資訊"

    nickname = app._last_role_name or app._last_slug
    coro_factory = functools.partial(
        run_with_session_context,
        state,
        functools.partial(_download_nikke_with_ctx, checklist_items=checklist_items, nickname=nickname),
    )
    ok = app._run_coro_sync(coro_factory)
    if not ok:
        return False, "下載失敗"
    return True, ""


async def _download_nikke_with_ctx(ctx, checklist_items: List[Tuple[int, str]], nickname: str) -> bool:
    name_codes = await fetch_user_characters(ctx)
    if not name_codes:
        return False
    filtered = _filter_checklist_items(checklist_items, name_codes)
    if not filtered:
        return False
    return await fetch_user_character_details(
        ctx,
        checklist_items=filtered,
        nickname=nickname,
        intl_open_id=None,
    )


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


