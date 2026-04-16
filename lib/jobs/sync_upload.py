# -*- coding: utf-8 -*-
"""Headless synchro upload job."""

from __future__ import annotations

import base64
import functools
import json
import os
from typing import Any, Dict, List, Optional

from ..api import fetch_guild_members
from ..data_store import load_union_account_map
from ..google_sheets.auth import get_gspread_client
from ..log import log_exc
from ..utils import load_session, run_with_session_context
from .common import JobResult, resolve_account, run_coro_sync
from .nikke_upload import resolve_character_sheet_target


def _clean_union_account_map(raw: Any) -> List[Dict[str, str]]:
    if not isinstance(raw, list):
        return []

    cleaned: List[Dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        union_id = str(item.get("union_id", "")).strip()
        username = str(item.get("username", "")).strip()
        if not union_id or not username:
            continue
        cleaned.append({"union_id": union_id, "username": username})
    return cleaned


def _parse_union_account_map_text(raw_text: str) -> List[Dict[str, str]]:
    try:
        raw = json.loads(raw_text)
    except Exception:
        return []
    return _clean_union_account_map(raw)


def _parse_union_account_map_base64(raw_text: str) -> List[Dict[str, str]]:
    try:
        decoded = base64.b64decode(raw_text.encode("utf-8"), validate=True).decode("utf-8")
    except Exception:
        return []
    return _parse_union_account_map_text(decoded)


def _load_union_account_map_from_env() -> List[Dict[str, str]]:
    raw_text = str(os.getenv("BLABLALINK_UNION_ACCOUNT_MAP_JSON", "")).strip()
    if not raw_text:
        return load_union_account_map()

    items = _parse_union_account_map_text(raw_text)
    if items:
        return items

    items = _parse_union_account_map_base64(raw_text)
    if items:
        return items

    return load_union_account_map()


def _resolve_accounts(scope: str, account: str | None) -> List[str]:
    scope_text = str(scope or "single").strip().lower() or "single"
    if scope_text == "single":
        slug = resolve_account(account)
        return [slug] if slug else []

    if scope_text != "all":
        return []

    items = _load_union_account_map_from_env()
    accounts: List[str] = []
    seen: set[str] = set()
    for item in items:
        username = str(item.get("username", "")).strip()
        if not username or username in seen:
            continue
        seen.add(username)
        accounts.append(username)
    return accounts


def _parse_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except Exception:
        return None


def _serialize_result(result: JobResult) -> Dict[str, Any]:
    return {
        "ok": result.ok,
        "message": result.message,
        "stats": result.stats,
        "outputs": result.outputs,
    }


def _run_sync_upload_single(account: str | None = None, worksheet_name: str = "成員名稱") -> JobResult:
    slug = resolve_account(account)
    if not slug:
        return JobResult(ok=False, message="無法解析帳號")

    state = load_session(slug)
    if not state:
        return JobResult(ok=False, message="找不到對應的 Session")

    try:
        guild_id, members = run_coro_sync(functools.partial(run_with_session_context, state, fetch_guild_members))
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return JobResult(ok=False, message=str(exc))

    if not guild_id:
        return JobResult(ok=False, message="找不到工會資訊")
    if not isinstance(members, list) or not members:
        return JobResult(ok=False, message="找不到成員資訊")

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
        return JobResult(ok=False, message="沒有符合條件的成員等級資料")

    try:
        sheet_id, _sheet_name = resolve_character_sheet_target(guild_id)
    except ValueError as exc:
        return JobResult(ok=True, message=f"試算表找不到: {str(exc)}")

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
            return JobResult(ok=False, message="沒有符合的成員名稱")

        ws.batch_update(updates, value_input_option="RAW")
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return JobResult(ok=False, message=str(exc))

    return JobResult(
        ok=True,
        message=f"上傳完成 ({matched_count}/{total_count})",
        stats={"account": slug, "matched_count": matched_count, "total_count": total_count},
        outputs={"sheet_id": sheet_id, "worksheet_name": worksheet_name, "union_id": guild_id},
    )


def run_sync_upload(
    account: str | None = None,
    worksheet_name: str = "成員名稱",
    scope: str = "single",
) -> JobResult:
    scope_text = str(scope or "single").strip().lower() or "single"
    if scope_text not in {"single", "all"}:
        return JobResult(ok=False, message=f"Unsupported scope: {scope}")

    accounts = _resolve_accounts(scope_text, account)
    if not accounts:
        if scope_text == "all":
            return JobResult(ok=False, message="No accounts found in BLABLALINK_UNION_ACCOUNT_MAP_JSON")
        return JobResult(ok=False, message="Missing account")

    if scope_text == "single":
        return _run_sync_upload_single(account=accounts[0], worksheet_name=worksheet_name)

    results: List[Dict[str, Any]] = []
    ok_count = 0

    for slug in accounts:
        result = _run_sync_upload_single(account=slug, worksheet_name=worksheet_name)
        entry = {
            "account": slug,
            **_serialize_result(result),
        }
        if result.ok:
            ok_count += 1
        results.append(entry)

    failed_count = len(results) - ok_count
    summary = {
        "scope": scope_text,
        "total": len(results),
        "ok_count": ok_count,
        "failed_count": failed_count,
    }
    if failed_count:
        message = f"sync upload finished with failures: ok={ok_count} failed={failed_count}"
    else:
        message = f"sync upload finished: ok={ok_count}"

    return JobResult(
        ok=failed_count == 0,
        message=message,
        stats=summary,
        outputs={"results": results},
    )
