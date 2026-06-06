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


def _normalize_sync_members(members: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for item in members:
        if not isinstance(item, dict):
            continue
        nickname = str(item.get("nickname", "")).strip()
        if not nickname:
            continue
        level = _parse_int(item.get("synchro_level"))
        if level is None:
            continue
        normalized.append(
            {
                "nickname": nickname,
                "member_id": str(item.get("member_id", "") or "").strip(),
                "synchro_level": level,
            }
        )
    return normalized


def _last_data_row(a_values: List[Any], c_values: List[Any]) -> int:
    last_row = 1
    max_row = max(len(a_values), len(c_values))
    for row_idx in range(2, max_row + 1):
        name = str(a_values[row_idx - 1]).strip() if row_idx - 1 < len(a_values) else ""
        member_id = str(c_values[row_idx - 1]).strip() if row_idx - 1 < len(c_values) else ""
        if name or member_id:
            last_row = row_idx
    return last_row


def _sync_member_levels_to_worksheet(ws: Any, members: List[Dict[str, Any]]) -> Dict[str, int]:
    sync_members = _normalize_sync_members(members)
    total_count = len(sync_members)
    if total_count == 0:
        return {"matched_count": 0, "appended_count": 0, "updated_count": 0, "total_count": 0}

    a_values = ws.col_values(1)
    c_values = ws.col_values(3)
    data_last_row = _last_data_row(a_values, c_values)
    clear_last_row = max(2, data_last_row)

    existing_name_to_row: Dict[str, int] = {}
    existing_member_id_to_row: Dict[str, int] = {}
    for row_idx in range(2, clear_last_row + 1):
        name = str(a_values[row_idx - 1]).strip() if row_idx - 1 < len(a_values) else ""
        member_id = str(c_values[row_idx - 1]).strip() if row_idx - 1 < len(c_values) else ""
        if name and name not in existing_name_to_row:
            existing_name_to_row[name] = row_idx
        if member_id and member_id not in existing_member_id_to_row:
            existing_member_id_to_row[member_id] = row_idx

    clear_updates: List[Dict[str, Any]] = [
        {"range": f"B2:B{clear_last_row}", "values": [[""] for _ in range(clear_last_row - 1)]}
    ]
    updates: List[Dict[str, Any]] = []
    matched_count = 0
    appended_count = 0
    used_rows: set[int] = set()
    next_append_row = max(2, data_last_row + 1)

    for member in sync_members:
        nickname = str(member["nickname"])
        member_id = str(member.get("member_id", "") or "").strip()
        level = int(member["synchro_level"])

        row_idx: Optional[int] = None
        if member_id:
            row_idx = existing_member_id_to_row.get(member_id)
        if row_idx is None:
            row_idx = existing_name_to_row.get(nickname)

        if row_idx is not None and row_idx not in used_rows:
            if member_id:
                updates.append({"range": f"B{row_idx}:C{row_idx}", "values": [[level, member_id]]})
            else:
                updates.append({"range": f"B{row_idx}:B{row_idx}", "values": [[level]]})
            used_rows.add(row_idx)
            matched_count += 1
            continue

        row_idx = next_append_row
        next_append_row += 1
        values: List[Any] = [nickname, level]
        range_end = "B"
        if member_id:
            values.append(member_id)
            range_end = "C"
        updates.append({"range": f"A{row_idx}:{range_end}{row_idx}", "values": [values]})
        used_rows.add(row_idx)
        appended_count += 1

    ws.batch_update(clear_updates, value_input_option="RAW")
    if updates:
        ws.batch_update(updates, value_input_option="RAW")
    return {
        "matched_count": matched_count,
        "appended_count": appended_count,
        "updated_count": matched_count + appended_count,
        "total_count": total_count,
    }


def _is_soft_skip_result(result: JobResult) -> bool:
    return str(result.message or "").strip() in {"找不到工會資訊", "找不到聯盟資訊"}


def _failure_entry(account: str, result: JobResult) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "account": account,
        "message": str(result.message or "").strip(),
    }
    if result.stats:
        entry["stats"] = result.stats
    return entry


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

    sync_members = _normalize_sync_members(members)
    total_count = len(sync_members)
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
        stats = _sync_member_levels_to_worksheet(ws, sync_members)
        updated_count = int(stats["updated_count"])
        matched_count = int(stats["matched_count"])
        appended_count = int(stats["appended_count"])
        total_count = int(stats["total_count"])

        if updated_count == 0:
            return JobResult(ok=False, message="沒有符合的成員名稱或 member_id")
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return JobResult(ok=False, message=str(exc))

    return JobResult(
        ok=True,
        message=f"上傳完成 ({updated_count}/{total_count}, 新增 {appended_count})",
        stats={
            "account": slug,
            "matched_count": matched_count,
            "appended_count": appended_count,
            "updated_count": updated_count,
            "total_count": total_count,
        },
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

    failures: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    ok_count = 0
    skipped_count = 0
    updated_count = 0
    total_count = 0

    for slug in accounts:
        result = _run_sync_upload_single(account=slug, worksheet_name=worksheet_name)
        if result.ok:
            ok_count += 1
            updated_count += int(result.stats.get("updated_count", 0) or 0)
            total_count += int(result.stats.get("total_count", 0) or 0)
        elif _is_soft_skip_result(result):
            skipped_count += 1
            skipped.append({"account": slug, "message": str(result.message or "").strip()})
        else:
            failures.append(_failure_entry(slug, result))

    failed_count = len(failures)
    summary = {
        "scope": scope_text,
        "total": len(accounts),
        "ok_count": ok_count,
        "skipped_count": skipped_count,
        "failed_count": failed_count,
        "updated_count": updated_count,
        "total_count": total_count,
    }
    if failed_count:
        message = f"上傳同步完成 ({updated_count}/{total_count})，失敗 {failed_count} 筆"
    else:
        message = f"上傳同步成功 ({updated_count}/{total_count})"

    return JobResult(
        ok=failed_count == 0,
        message=message,
        stats=summary,
        outputs={"failures": failures, "skipped": skipped},
    )
