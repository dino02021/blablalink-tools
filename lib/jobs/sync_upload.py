# -*- coding: utf-8 -*-
"""Headless synchro upload job."""

from __future__ import annotations

import base64
import functools
import json
import os
from typing import Any, Dict, List, Optional, Tuple

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


def _exception_message(exc: Exception) -> str:
    return str(exc).strip() or repr(exc)


def _normalize_sync_members(members: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for item in members:
        if not isinstance(item, dict):
            continue
        nickname = str(item.get("nickname", "") or "").strip()
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


def _character_sheet_start_row(ws: Any) -> int:
    return 4 if str(getattr(ws, "title", "")).strip() == "角色練度_變化" else 3


def _plan_member_row_compaction(
    sheet: Any,
    start_row: int,
    source_last_row: int,
    source_rows: List[Optional[int]],
    final_count: int,
) -> List[Dict[str, Any]]:
    """Move complete rows, then clear departed members before adding anyone."""
    requests: List[Dict[str, Any]] = []
    required_rows = max(source_last_row, start_row + final_count - 1, start_row)
    if required_rows > sheet.row_count:
        requests.append(
            {"appendDimension": {"sheetId": sheet.id, "dimension": "ROWS", "length": required_rows - sheet.row_count}}
        )

    row_order: List[Optional[int]] = list(range(start_row, source_last_row + 1))
    inserted_count = 0
    for offset, source_row in enumerate(source_rows):
        target_index = start_row - 1 + offset
        if source_row is None:
            requests.append(
                {
                    "insertDimension": {
                        "range": {
                            "sheetId": sheet.id,
                            "dimension": "ROWS",
                            "startIndex": target_index,
                            "endIndex": target_index + 1,
                        },
                        "inheritFromBefore": True,
                    }
                }
            )
            row_order.insert(offset, None)
            inserted_count += 1
            continue

        source_offset = row_order.index(source_row)
        if source_offset == offset:
            continue
        requests.append(
            {
                "moveDimension": {
                    "source": {
                        "sheetId": sheet.id,
                        "dimension": "ROWS",
                        "startIndex": start_row - 1 + source_offset,
                        "endIndex": start_row + source_offset,
                    },
                    "destinationIndex": target_index,
                }
            }
        )
        row_order.insert(offset, row_order.pop(source_offset))

    clear_start_row = start_row + len(source_rows)
    clear_last_row = max(source_last_row + inserted_count, start_row + final_count - 1)
    if clear_start_row <= clear_last_row:
        requests.append(
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet.id,
                        "startRowIndex": clear_start_row - 1,
                        "endRowIndex": clear_last_row,
                    },
                    "cell": {},
                    "fields": "userEnteredValue,note",
                }
            }
        )
    return requests


def _character_source_rows(
    raw_values: List[List[Any]],
    member_entries: List[Tuple[Optional[int], Dict[str, Any]]],
    member_names: List[Any],
    start_row: int,
) -> List[Optional[int]]:
    name_to_rows: Dict[str, List[int]] = {}
    for row_idx, row in enumerate(raw_values, start=1):
        name = str(row[0]).strip() if row else ""
        if row_idx >= start_row and name:
            name_to_rows.setdefault(name, []).append(row_idx)

    source_rows: List[Optional[int]] = []
    used_rows: set[int] = set()
    for member_row, member in member_entries:
        if member_row is None:
            continue
        old_name = str(member_names[member_row - 1]).strip()
        nickname = str(member["nickname"])
        aligned_row = start_row + member_row - 2
        aligned_name = ""
        if aligned_row <= len(raw_values) and raw_values[aligned_row - 1]:
            aligned_name = str(raw_values[aligned_row - 1][0]).strip()

        source_row: Optional[int] = None
        # Equal display names are disambiguated by their original member offset.
        if aligned_row not in used_rows and aligned_name in {old_name, nickname}:
            source_row = aligned_row
        else:
            for name in (old_name, nickname):
                source_row = next((row for row in name_to_rows.get(name, []) if row not in used_rows), None)
                if source_row is not None:
                    break
        if source_row is None and not aligned_name and aligned_row not in used_rows:
            source_row = aligned_row
        if source_row is not None:
            used_rows.add(source_row)
        source_rows.append(source_row)
    return source_rows


def _member_value_update(sheet: Any, row: int, column: int, values: List[Any]) -> Dict[str, Any]:
    cells = []
    for value in values:
        value_type = "numberValue" if isinstance(value, int) else "stringValue"
        cells.append({"userEnteredValue": {value_type: value}})
    return {
        "updateCells": {
            "start": {"sheetId": sheet.id, "rowIndex": row - 1, "columnIndex": column - 1},
            "rows": [{"values": cells}],
            "fields": "userEnteredValue",
        }
    }


def _sync_member_levels_to_worksheet(
    ws: Any,
    members: List[Dict[str, Any]],
    character_sheets: Optional[List[Any]] = None,
) -> Dict[str, int]:
    sync_members = _normalize_sync_members(members)
    total_count = len(sync_members)
    if total_count == 0:
        return {"matched_count": 0, "appended_count": 0, "updated_count": 0, "total_count": 0}
    if total_count != len(members):
        raise ValueError("成員名單資料不完整，停止同步以免誤移除仍在聯盟的成員")
    member_ids = [member["member_id"] for member in sync_members if member["member_id"]]
    if len(member_ids) != len(set(member_ids)):
        raise ValueError("成員名單包含重複 Blabla ID，停止同步")

    # Every source must be readable before any membership or grid changes.
    member_values = ws.get_all_values()
    character_values = [(sheet, sheet.get_all_values()) for sheet in (character_sheets or [])]
    a_values = [row[0] if row else "" for row in member_values]
    c_values = [row[2] if len(row) > 2 else "" for row in member_values]
    data_last_row = _last_data_row(a_values, c_values)
    existing_name_to_row: Dict[str, int] = {}
    existing_member_id_to_row: Dict[str, int] = {}
    for row_idx in range(2, data_last_row + 1):
        name = str(a_values[row_idx - 1]).strip()
        member_id = str(c_values[row_idx - 1]).strip()
        if name:
            existing_name_to_row.setdefault(name, row_idx)
        if member_id:
            existing_member_id_to_row.setdefault(member_id, row_idx)

    matched_members_by_row: Dict[int, Dict[str, Any]] = {}
    unmatched_members: List[Dict[str, Any]] = []
    for member in sync_members:
        member_id = member["member_id"]
        row_idx = existing_member_id_to_row.get(member_id) if member_id else None
        if row_idx is None:
            row_idx = existing_name_to_row.get(member["nickname"])
        if row_idx is not None and row_idx not in matched_members_by_row:
            matched_members_by_row[row_idx] = member
        else:
            unmatched_members.append(member)

    member_entries: List[Tuple[Optional[int], Dict[str, Any]]] = [
        (row, matched_members_by_row[row]) for row in sorted(matched_members_by_row)
    ]
    member_entries.extend((None, member) for member in unmatched_members)
    source_rows = [row for row, _member in member_entries if row is not None]
    requests = _plan_member_row_compaction(ws, 2, data_last_row, source_rows, total_count)
    retained_updates: List[Dict[str, Any]] = []
    new_updates: List[Dict[str, Any]] = []
    for offset, (source_row, member) in enumerate(member_entries):
        if source_row is not None and not member["member_id"]:
            retained_updates.append(_member_value_update(ws, offset + 2, 2, [member["synchro_level"]]))
            continue
        update = _member_value_update(
            ws, offset + 2, 1, [member["nickname"], member["synchro_level"], member["member_id"]]
        )
        if source_row is None:
            new_updates.append(update)
        else:
            retained_updates.append(update)

    for sheet, raw_values in character_values:
        start_row = _character_sheet_start_row(sheet)
        source_last_row = max(start_row + data_last_row - 2, len(raw_values))
        character_rows = _character_source_rows(raw_values, member_entries, a_values, start_row)
        requests.extend(_plan_member_row_compaction(sheet, start_row, source_last_row, character_rows, total_count))
        for offset, (source_row, member) in enumerate(member_entries):
            update = _member_value_update(sheet, start_row + offset, 1, [member["nickname"]])
            if source_row is None:
                new_updates.append(update)
            else:
                retained_updates.append(update)

    # One atomic request prevents a failed sheet from leaving the others shifted.
    # New-member writes run only after every sheet's departed rows are cleared.
    ws.spreadsheet.batch_update({"requests": requests + retained_updates + new_updates})
    matched_count = len(matched_members_by_row)
    appended_count = len(unmatched_members)
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


def _is_valid_sync_success(result: JobResult) -> bool:
    return result.ok and bool(result.stats)


def _union_label(item: Dict[str, Any]) -> str:
    sheet_name = str(item.get("sheet_name", "") or "").strip()
    union_id = str(item.get("union_id", "") or "").strip()
    if sheet_name and union_id:
        return f"{sheet_name} ({union_id})"
    return sheet_name or union_id or "-"


def _build_sync_message(
    updated_count: int,
    total_count: int,
    union_summaries: List[Dict[str, Any]],
    failures: List[Dict[str, Any]],
    skipped: List[Dict[str, Any]],
) -> str:
    status = "完成" if failures or skipped else "成功"
    lines = [f"上傳同步{status} ({updated_count}/{total_count})"]
    for item in union_summaries:
        lines.append(f"{_union_label(item)}: {item.get('updated_count', 0)}/{item.get('total_count', 0)}")
    if failures:
        lines.append("失敗:")
        for item in failures:
            lines.append(f"- {_union_label(item)} / {item.get('account', '-')}: {item.get('message', '')}")
    if skipped:
        lines.append("跳過:")
        for item in skipped:
            lines.append(f"- {item.get('account', '-')}: {item.get('message', '')}")
    return "\n".join(lines)


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
        return JobResult(ok=False, message=_exception_message(exc))

    if not guild_id:
        return JobResult(ok=False, message="找不到工會資訊")
    if not isinstance(members, list) or not members:
        return JobResult(ok=False, message="找不到成員資訊")

    sync_members = _normalize_sync_members(members)
    total_count = len(sync_members)
    if total_count == 0:
        return JobResult(ok=False, message="沒有符合條件的成員等級資料")

    try:
        sheet_id, sheet_name = resolve_character_sheet_target(guild_id)
    except ValueError as exc:
        return JobResult(ok=True, message=f"試算表找不到: {str(exc)}")

    try:
        client = get_gspread_client()
        sheet = client.open_by_key(sheet_id)
        ws = sheet.worksheet(worksheet_name)
        character_titles = [
            "角色練度_電擊", "角色練度_水冷", "角色練度_燃燒",
            "角色練度_風壓", "角色練度_鐵甲", "角色練度_變化",
        ]
        sheets_by_title = {candidate.title: candidate for candidate in sheet.worksheets()}
        missing_titles = [title for title in character_titles if title not in sheets_by_title]
        if missing_titles:
            raise ValueError(f"缺少角色練度工作表，停止同步：{'、'.join(missing_titles)}")
        character_sheets = [sheets_by_title[title] for title in character_titles]
        stats = _sync_member_levels_to_worksheet(
            ws,
            members,
            character_sheets=character_sheets,
        )
        updated_count = int(stats["updated_count"])
        matched_count = int(stats["matched_count"])
        appended_count = int(stats["appended_count"])
        total_count = int(stats["total_count"])

        if updated_count == 0:
            return JobResult(ok=False, message="沒有符合的成員名稱或 member_id")
    except PermissionError as exc:
        log_exc(exc)
        target = f"{sheet_name} ({sheet_id})" if sheet_name else sheet_id
        return JobResult(
            ok=False,
            message=f"無法存取試算表 {target}：Google 服務帳號沒有權限",
            outputs={"sheet_id": sheet_id, "sheet_name": sheet_name, "union_id": guild_id},
        )
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return JobResult(ok=False, message=_exception_message(exc))

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
        outputs={"sheet_id": sheet_id, "sheet_name": sheet_name, "worksheet_name": worksheet_name, "union_id": guild_id},
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
        try:
            result = _run_sync_upload_single(account=accounts[0], worksheet_name=worksheet_name)
        except Exception as exc:  # noqa: BLE001
            log_exc(exc)
            return JobResult(ok=False, message=_exception_message(exc))
        if result.ok and not _is_valid_sync_success(result):
            return JobResult(ok=False, message=result.message, stats=result.stats, outputs=result.outputs)
        return result

    failures: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    union_summaries: List[Dict[str, Any]] = []
    ok_count = 0
    skipped_count = 0
    updated_count = 0
    total_count = 0

    for slug in accounts:
        try:
            result = _run_sync_upload_single(account=slug, worksheet_name=worksheet_name)
        except Exception as exc:  # noqa: BLE001
            log_exc(exc)
            failures.append({"account": slug, "message": _exception_message(exc)})
            continue

        if _is_valid_sync_success(result):
            ok_count += 1
            account_updated_count = int(result.stats.get("updated_count", 0) or 0)
            account_total_count = int(result.stats.get("total_count", 0) or 0)
            updated_count += account_updated_count
            total_count += account_total_count
            union_summaries.append(
                {
                    "account": slug,
                    "union_id": result.outputs.get("union_id", ""),
                    "sheet_name": result.outputs.get("sheet_name", ""),
                    "updated_count": account_updated_count,
                    "total_count": account_total_count,
                }
            )
        elif _is_soft_skip_result(result):
            skipped_count += 1
            skipped.append({"account": slug, "message": str(result.message or "").strip()})
        else:
            entry = _failure_entry(slug, result)
            entry["union_id"] = result.outputs.get("union_id", "")
            entry["sheet_name"] = result.outputs.get("sheet_name", "")
            failures.append(entry)

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
    message = _build_sync_message(updated_count, total_count, union_summaries, failures, skipped)

    return JobResult(
        ok=failed_count == 0 and skipped_count == 0,
        message=message,
        stats=summary,
        outputs={"_summary_only": True, "unions": union_summaries, "failures": failures, "skipped": skipped},
    )
