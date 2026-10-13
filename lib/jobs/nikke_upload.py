# -*- coding: utf-8 -*-
"""Headless Nikke upload job."""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from ..api import fetch_union_info
from ..constants import CHAR_DETAILS_DIR, CHAR_DETAILS_TYPE
from ..data_store import load_sheets
from ..healthcheck_config import (
    HealthcheckSnapshot,
    ensure_healthcheck_snapshot_current,
    load_healthcheck_snapshot,
    validate_download_context,
    validate_training_headers,
)
from ..log import log_exc
from ..utils import load_session, run_with_session_context
from .common import JobResult, resolve_account, run_coro_sync


def _find_character_sheet_by_sheet_id(sheet_id: str) -> Tuple[str, str]:
    target = str(sheet_id or "").strip()
    if not target:
        return "", ""

    sheets, _ = load_sheets()
    for item in sheets:
        if str(item.get("type", "") or "").strip() != CHAR_DETAILS_TYPE:
            continue
        if str(item.get("sheet_id", "") or "").strip() != target:
            continue
        return target, str(item.get("sheet_name", "") or "").strip()
    return "", ""


def _find_character_sheet_by_union_id(union_id: str) -> Tuple[str, str]:
    target_union_id = str(union_id or "").strip()
    if not target_union_id:
        return "", ""

    sheets, _ = load_sheets()
    for item in sheets:
        if str(item.get("type", "") or "").strip() != CHAR_DETAILS_TYPE:
            continue
        if str(item.get("union_id", "") or "").strip() != target_union_id:
            continue
        return (
            str(item.get("sheet_id", "") or "").strip(),
            str(item.get("sheet_name", "") or "").strip(),
        )
    return "", ""


def resolve_character_sheet_target(union_id: str, sheet_id: str | None = None) -> Tuple[str, str]:
    explicit_sheet_id = str(sheet_id or "").strip()
    if explicit_sheet_id:
        resolved_sheet_id, resolved_sheet_name = _find_character_sheet_by_sheet_id(explicit_sheet_id)
        if not resolved_sheet_id:
            raise ValueError("找不到指定的 CharacterDetails 試算表")
        return resolved_sheet_id, resolved_sheet_name

    resolved_sheet_id, resolved_sheet_name = _find_character_sheet_by_union_id(union_id)
    if not resolved_sheet_id:
        raise ValueError(f"找不到 union_id={union_id or '-'} 對應的 CharacterDetails 試算表")
    return resolved_sheet_id, resolved_sheet_name


def resolve_character_sheet_for_account(account: str | None, sheet_id: str | None = None) -> Tuple[str, str, str]:
    slug = resolve_account(account)
    if not slug:
        raise ValueError("缺少帳號")

    state = load_session(slug)
    if not state:
        raise ValueError("找不到登入 Session")

    try:
        union_id, _intl_open_id, _nikke_area_id = run_coro_sync(
            functools.partial(run_with_session_context, state, fetch_union_info)
        )
    except Exception as exc:  # noqa: BLE001
        raise ValueError(str(exc)) from exc

    target_union_id = str(union_id or "").strip()
    if not target_union_id:
        raise ValueError("找不到聯盟資訊")

    resolved_sheet_id, resolved_sheet_name = resolve_character_sheet_target(target_union_id, sheet_id=sheet_id)
    return target_union_id, resolved_sheet_id, resolved_sheet_name


def _resolve_files(files: List[str] | None, input_dir: str | Path | None) -> Tuple[Path, List[str]]:
    base_dir = Path(input_dir) if input_dir else CHAR_DETAILS_DIR
    base_dir.mkdir(parents=True, exist_ok=True)
    if files:
        return base_dir, [str(item).strip() for item in files if str(item).strip()]
    return base_dir, [item.name for item in sorted(base_dir.glob("*.json"))]


def run_nikke_upload(
    account: str | None = None,
    files: List[str] | None = None,
    input_dir: str | Path | None = None,
    sheet_id: str | None = None,
    healthcheck_snapshot: HealthcheckSnapshot | None = None,
) -> JobResult:
    try:
        union_id, target_sheet_id, target_sheet_name = resolve_character_sheet_for_account(account, sheet_id=sheet_id)
    except ValueError as exc:
        return JobResult(ok=False, message=str(exc))

    base_dir, target_files = _resolve_files(files, input_dir)
    if not target_files:
        return JobResult(ok=False, message="沒有可上傳的資料")

    try:
        from ..google_sheets.auth import get_gspread_client
        from ..gui.dialogs.nikke_upload import (
            _apply_updates,
            _build_updates,
            _collect_character_sheets,
            _find_change_sheet,
            _find_member_sheet,
            _load_change_headers,
            _load_sheet_headers,
            _notify_web_app_batch,
            _populate_change_updates,
        )
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return JobResult(ok=False, message=f"載入 nikke upload 失敗: {exc}")

    ok = False
    ok_count = 0
    total_count = len(target_files)
    failed_files: List[Dict[str, str]] = []
    msg = ""
    try:
        cli = get_gspread_client()
        sh = cli.open_by_key(target_sheet_id)
        snapshot = healthcheck_snapshot or load_healthcheck_snapshot()
        ensure_healthcheck_snapshot_current(snapshot)
        header_fingerprint = validate_training_headers(sh, snapshot)

        worksheets = sh.worksheets()
        main_sheets = _collect_character_sheets(worksheets)
        if not main_sheets:
            return JobResult(ok=False, message="目標試算表缺少角色練度工作表")
        change_sheet = _find_change_sheet(worksheets)
        member_sheet = _find_member_sheet(worksheets)
        change_nick_to_row, change_header_pos = _load_change_headers(change_sheet)

        nick_to_row_by_ws, member_id_to_nick, member_id_to_row_by_ws, header_pos = _load_sheet_headers(
            main_sheets,
            member_sheet=member_sheet,
        )
        if not nick_to_row_by_ws and not member_id_to_nick and not member_id_to_row_by_ws:
            return JobResult(ok=False, message="角色練度工作表缺少成員名稱或 Blabla ID 欄位")

        combined_updates: Dict[Any, List[Tuple[int, int, List[Any]]]] = {}
        combined_changes: Dict[Any, List[Tuple[int, int, List[Any]]]] = {}
        pending: List[Tuple[Any, int, int, List[Any], str, str]] = []
        for fname in target_files:
            data_file = base_dir / fname
            if not data_file.exists():
                msg = f"找不到資料檔：{fname}"
                failed_files.append({"file": fname, "message": "找不到資料檔"})
                continue
            try:
                obj = json.loads(data_file.read_text(encoding="utf-8"))
            except Exception:
                msg = f"讀取資料檔失敗：{fname}"
                failed_files.append({"file": fname, "message": "讀取資料檔失敗"})
                continue

            nickname = obj.get("nickname", "")
            member_id = str(obj.get("member_id", "") or "").strip()
            details = obj.get("character_details", [])
            if not nickname or not isinstance(details, list):
                msg = f"資料檔格式錯誤：{fname}"
                failed_files.append({"file": fname, "message": "資料檔格式錯誤"})
                continue

            try:
                validate_download_context(obj, snapshot, target_sheet_id, header_fingerprint)
            except ValueError as exc:
                raise ValueError(f"{fname}: {exc}") from exc

            updates_by_ws, changes_by_ws = _build_updates(
                details,
                nickname,
                member_id,
                main_sheets,
                nick_to_row_by_ws,
                member_id_to_nick,
                member_id_to_row_by_ws,
                header_pos,
                change_sheet,
                change_nick_to_row,
                change_header_pos,
            )
            for ws, items in updates_by_ws.items():
                combined_updates.setdefault(ws, []).extend(items)
            pending.extend(changes_by_ws)
            if updates_by_ws:
                ok_count += 1
            else:
                failed_files.append(
                    {
                        "file": fname,
                        "nickname": str(nickname),
                        "message": "找不到成員列或角色欄位",
                    }
                )

        _populate_change_updates(
            pending,
            combined_changes,
            change_sheet,
            change_nick_to_row,
            change_header_pos,
        )
        ensure_healthcheck_snapshot_current(snapshot)
        if validate_training_headers(sh, snapshot) != header_fingerprint:
            raise ValueError("角色練度表頭已變更，請重新下載後再上傳")
        ok, notify_items = _apply_updates(combined_updates)
        if change_sheet and combined_changes:
            change_ok, change_notify_items = _apply_updates(combined_changes)
            ok = ok or change_ok
            notify_items.extend(change_notify_items)
        if notify_items:
            _notify_web_app_batch(notify_items, spreadsheet_id=target_sheet_id)
        if not ok:
            msg = msg or "批次上傳失敗"
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return JobResult(ok=False, message=str(exc))

    failed_count = len(failed_files)
    outputs = {
        "input_dir": str(base_dir),
        "sheet_id": target_sheet_id,
        "sheet_name": target_sheet_name,
        "union_id": union_id,
        "failed_files": failed_files,
    }
    if not ok:
        if not failed_files:
            failed_files.append({"message": msg or "批次上傳失敗"})
            failed_count = len(failed_files)
        return JobResult(
            ok=False,
            message=msg or "上傳妮姬資料失敗",
            stats={"ok_count": ok_count, "total_count": total_count, "failed_count": failed_count},
            outputs=outputs,
        )

    if failed_files:
        return JobResult(
            ok=False,
            message=f"上傳妮姬資料完成 ({ok_count}/{total_count})，失敗 {failed_count} 筆",
            stats={"ok_count": ok_count, "total_count": total_count, "failed_count": failed_count},
            outputs=outputs,
        )

    return JobResult(
        ok=True,
        message=f"上傳妮姬資料成功 ({ok_count}/{total_count})",
        stats={"ok_count": ok_count, "total_count": total_count, "failed_count": 0},
        outputs=outputs,
    )
