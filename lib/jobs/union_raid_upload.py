# -*- coding: utf-8 -*-
"""Headless union raid upload job."""

from __future__ import annotations

import functools
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..api import fetch_guild_members
from ..constants import GOOGLE_CRED_FILE, GOOGLE_SCOPES, UNION_RAID_TEMP_FILE, UNION_RAID_TYPE
from ..data_store import load_sheets
from ..log import log_exc
from ..utils import load_session, run_with_session_context
from .common import JobResult, resolve_account, run_coro_sync


def _should_fail_on_webapp_error() -> bool:
    return str(os.getenv("BLABLALINK_STRICT_UNION_WEBAPP", "")).strip().lower() in {"1", "true", "yes", "on"}


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


def _load_union_payload(path: Path) -> Tuple[str, List[Dict[str, Any]]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return "", []

    if not isinstance(data, dict):
        return "", []

    union_id = str(data.get("union_id", "") or "").strip()
    items = data.get("participate_data")
    if not isinstance(items, list):
        return union_id, []

    cleaned = [item for item in items if isinstance(item, dict)]
    return union_id, cleaned


def _find_sheet_by_sheet_id(sheet_id: str) -> Tuple[str, str]:
    target = str(sheet_id or "").strip()
    if not target:
        return "", ""

    sheets, _ = load_sheets()
    for item in sheets:
        if str(item.get("sheet_id", "") or "").strip() != target:
            continue
        return target, str(item.get("sheet_name", "") or "").strip()
    return target, ""


def _find_sheet_by_union_id(union_id: str) -> Tuple[str, str]:
    target_union_id = str(union_id or "").strip()
    if not target_union_id:
        return "", ""

    sheets, _ = load_sheets()
    for item in sheets:
        if str(item.get("type", "") or "").strip() != UNION_RAID_TYPE:
            continue
        if str(item.get("union_id", "") or "").strip() != target_union_id:
            continue
        return (
            str(item.get("sheet_id", "") or "").strip(),
            str(item.get("sheet_name", "") or "").strip(),
        )
    return "", ""


def resolve_union_sheet_target(
    data_path: str | Path | None = None,
    sheet_id: str | None = None,
) -> Tuple[str, str, str]:
    target_data_path = Path(data_path) if data_path else _find_latest_union_raid_file()
    if target_data_path is None or not target_data_path.exists():
        raise ValueError("找不到聯盟突襲資料檔")

    union_id, items = _load_union_payload(target_data_path)
    if not items:
        raise ValueError("聯盟突襲資料檔內容無效")

    explicit_sheet_id = str(sheet_id or "").strip()
    if explicit_sheet_id:
        resolved_sheet_id, resolved_sheet_name = _find_sheet_by_sheet_id(explicit_sheet_id)
        if not resolved_sheet_id:
            raise ValueError("找不到指定的試算表")
        return union_id, resolved_sheet_id, resolved_sheet_name

    resolved_sheet_id, resolved_sheet_name = _find_sheet_by_union_id(union_id)
    if not resolved_sheet_id:
        raise ValueError(f"找不到 union_id={union_id or '-'} 對應的試算表")
    return union_id, resolved_sheet_id, resolved_sheet_name


def resolve_latest_union_upload_month(
    data_path: str | Path | None = None,
    sheet_id: str | None = None,
) -> str:
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Missing Google Sheets runtime: {exc}") from exc

    _union_id, target_sheet_id, _target_sheet_name = resolve_union_sheet_target(
        data_path=data_path,
        sheet_id=sheet_id,
    )
    if not GOOGLE_CRED_FILE.exists():
        raise ValueError("Missing Google credentials file")

    try:
        creds = json.loads(GOOGLE_CRED_FILE.read_text(encoding="utf-8"))
        client = gspread.authorize(
            Credentials.from_service_account_info(creds, scopes=GOOGLE_SCOPES)
        )
        sh = client.open_by_key(target_sheet_id)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Failed to open target spreadsheet: {exc}") from exc

    months: List[str] = []
    seen: set[str] = set()
    for ws in sh.worksheets():
        title = str(ws.title).strip()
        match = re.match(r"^(\d{4}/\d{2})", title)
        if not match:
            continue
        value = match.group(1)
        if value in seen:
            continue
        seen.add(value)
        months.append(value)

    if not months:
        raise ValueError("No upload month found in target spreadsheet")

    months.sort(reverse=True)
    return months[0]


def run_union_raid_upload(
    account: str | None = None,
    month: str | None = None,
    data_path: str | Path | None = None,
    sheet_id: str | None = None,
) -> JobResult:
    slug = resolve_account(account)
    if not slug:
        return JobResult(ok=False, message="找不到可用帳號")

    month_text = str(month or "").strip()
    if not month_text:
        return JobResult(ok=False, message="請提供 month")

    target_data_path = Path(data_path) if data_path else _find_latest_union_raid_file()
    if target_data_path is None or not target_data_path.exists():
        return JobResult(ok=False, message="找不到聯盟突襲資料檔")

    union_id, items = _load_union_payload(target_data_path)
    if not items:
        return JobResult(ok=False, message="聯盟突襲資料檔內容無效")

    try:
        _resolved_union_id, target_sheet_id, target_sheet_name = resolve_union_sheet_target(
            data_path=target_data_path,
            sheet_id=sheet_id,
        )
    except ValueError as exc:
        return JobResult(ok=False, message=str(exc))

    state = load_session(slug)
    if not state:
        return JobResult(ok=False, message="找不到 Session")

    try:
        from ..gui.dialogs.union_raid_upload import _upload_to_sheets
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return JobResult(ok=False, message=f"載入 union upload 模組失敗: {exc}")

    try:
        _guild_id, fetched_members = run_coro_sync(
            functools.partial(run_with_session_context, state, fetch_guild_members)
        )
        guild_members = fetched_members if isinstance(fetched_members, list) else []
        if not guild_members:
            return JobResult(ok=False, message="抓不到公會成員資料")

        sheet_ok, web_ok, web_msg = _upload_to_sheets(target_sheet_id, month_text, items, guild_members)
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return JobResult(ok=False, message=str(exc))

    outputs = {
        "data_path": str(target_data_path),
        "sheet_id": target_sheet_id,
        "sheet_name": target_sheet_name,
        "month": month_text,
        "union_id": union_id,
    }
    if not sheet_ok:
        return JobResult(
            ok=False,
            message="上傳聯盟突襲資料到試算表失敗",
            stats={"web_ok": web_ok},
            outputs=outputs,
        )

    if not web_ok:
        if _should_fail_on_webapp_error():
            return JobResult(
                ok=False,
                message=f"試算表已更新，但 WebApp 失敗: {web_msg}",
                stats={"partial": True, "web_ok": False},
                outputs=outputs,
            )
        return JobResult(
            ok=True,
            message=f"試算表已更新，但 WebApp 失敗: {web_msg}",
            stats={"partial": True, "web_ok": False},
            outputs=outputs,
        )

    return JobResult(
        ok=True,
        message="聯盟突襲資料上傳成功",
        stats={"partial": False, "web_ok": True},
        outputs=outputs,
    )
