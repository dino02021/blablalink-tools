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
    if files is not None:
        return base_dir, [str(item).strip() for item in files if str(item).strip()]
    return base_dir, [item.name for item in sorted(base_dir.glob("*.json"))]


def run_nikke_upload(
    account: str | None = None,
    files: List[str] | None = None,
    input_dir: str | Path | None = None,
    sheet_id: str | None = None,
    healthcheck_snapshot: HealthcheckSnapshot | None = None,
    expected_members: List[Dict[str, Any]] | None = None,
) -> JobResult:
    try:
        union_id, target_sheet_id, target_sheet_name = resolve_character_sheet_for_account(account, sheet_id=sheet_id)
    except ValueError as exc:
        return JobResult(ok=False, message=str(exc))

    base_dir, target_files = _resolve_files(files, input_dir)
    total_count = len(expected_members) if expected_members is not None else len(target_files)
    if not target_files:
        return JobResult(ok=False, message="沒有可上傳的資料", stats={"ok_count": 0, "total_count": total_count, "failed_count": total_count})

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
            _read_training_values,
            _filter_unchanged_updates,
        )
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return JobResult(ok=False, message=f"載入 nikke upload 失敗: {exc}")

    ok_count = 0
    failed_files: List[Dict[str, str]] = []
    try:
        cli = get_gspread_client()
        sh = cli.open_by_key(target_sheet_id)
        snapshot = healthcheck_snapshot or load_healthcheck_snapshot()
        ensure_healthcheck_snapshot_current(snapshot)
        worksheets = sh.worksheets()
        main_sheets = _collect_character_sheets(worksheets)
        change_sheet = _find_change_sheet(worksheets)
        member_sheet = _find_member_sheet(worksheets)
        if len(main_sheets) != 5 or change_sheet is None or member_sheet is None:
            raise ValueError("目標試算表缺少五屬性練度表、變化表或成員名稱表")
        training_sheets = main_sheets + [change_sheet]
        values_by_ws = _read_training_values(sh, [member_sheet] + training_sheets)
        header_fingerprint = validate_training_headers(
            sh, snapshot, worksheets=training_sheets,
            values_by_sheet={ws.title: values for ws, values in values_by_ws.items()},
        )
        change_nick_to_row, change_header_pos = _load_change_headers(change_sheet, values_by_ws)

        nick_to_row_by_ws, member_id_to_nick, member_id_to_row_by_ws, header_pos = _load_sheet_headers(
            main_sheets,
            member_sheet=member_sheet,
            values_by_ws=values_by_ws,
        )
        if not nick_to_row_by_ws and not member_id_to_nick and not member_id_to_row_by_ws:
            return JobResult(ok=False, message="角色練度工作表缺少成員名稱或 Blabla ID 欄位")

        combined_updates: Dict[Any, List[Tuple[int, int, List[Any]]]] = {}
        combined_changes: Dict[Any, List[Tuple[int, int, List[Any]]]] = {}
        pending: List[Tuple[Any, int, int, List[Any], str, str]] = []
        expected_by_id = None
        if expected_members is not None:
            expected_by_id = {str(item.get("member_id", "")).strip(): item for item in expected_members}
            if "" in expected_by_id or len(expected_by_id) != len(expected_members):
                raise ValueError("本次下載的成員清單包含空白或重複 ID")
        uploaded_keys = set()
        for fname in target_files:
            data_file = base_dir / fname
            if not data_file.exists():
                failed_files.append({"file": fname, "message": "找不到資料檔"})
                continue
            try:
                obj = json.loads(data_file.read_text(encoding="utf-8"))
            except Exception:
                failed_files.append({"file": fname, "message": "讀取資料檔失敗"})
                continue

            if not isinstance(obj, dict):
                failed_files.append({"file": fname, "message": "資料檔格式錯誤"})
                continue

            nickname = obj.get("nickname", "")
            member_id = str(obj.get("member_id", "") or "").strip()
            details = obj.get("character_details", [])
            if not nickname or not isinstance(details, list):
                failed_files.append({"file": fname, "message": "資料檔格式錯誤"})
                continue

            try:
                validate_download_context(obj, snapshot, target_sheet_id, header_fingerprint)
            except ValueError as exc:
                raise ValueError(f"{fname}: {exc}") from exc

            member_key = member_id or str(nickname).strip()
            if member_key in uploaded_keys:
                raise ValueError(f"同一成員有重複的上傳資料檔：{nickname}")
            uploaded_keys.add(member_key)
            if expected_by_id is not None:
                expected = expected_by_id.get(member_id)
                if expected is None or {str(code) for code in expected.get("expected_name_codes", [])} != {str(detail["name_code"]) for detail in details}:
                    raise ValueError(f"{nickname} 的資料檔與本次下載名單或角色清單不同，請重新下載")

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
            if not updates_by_ws:
                failed_files.append(
                    {
                        "file": fname,
                        "nickname": str(nickname),
                        "message": "找不到成員列或角色欄位",
                    }
                )

        if expected_by_id is not None and uploaded_keys != set(expected_by_id):
            raise ValueError("上傳檔案未涵蓋本次應更新的完整成員名單，停止整批上傳")
        if failed_files:
            raise ValueError("資料檔不完整，停止整批上傳：" + "、".join(item["file"] for item in failed_files))
        _populate_change_updates(
            pending,
            combined_changes,
            change_sheet,
            change_nick_to_row,
            change_header_pos,
            values_by_ws=values_by_ws,
        )
        ensure_healthcheck_snapshot_current(snapshot)
        latest_values = _read_training_values(sh, [member_sheet] + training_sheets)
        if validate_training_headers(
            sh, snapshot, worksheets=training_sheets,
            values_by_sheet={ws.title: values for ws, values in latest_values.items()},
        ) != header_fingerprint:
            raise ValueError("角色練度表頭已變更，請重新下載後再上傳")
        if latest_values != values_by_ws:
            raise ValueError("上傳期間成員順序、練度或變化資料已變更，請重新讀取後再上傳")
        for ws, items in combined_changes.items():
            combined_updates.setdefault(ws, []).extend(items)
        is_applied, notify_items = _apply_updates(_filter_unchanged_updates(combined_updates, values_by_ws))
        if not is_applied:
            raise ValueError("批次上傳失敗")
        ok_count = total_count
        if notify_items:
            _notify_web_app_batch(notify_items, spreadsheet_id=target_sheet_id)
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return JobResult(
            ok=False, message=str(exc),
            stats={"ok_count": 0, "total_count": total_count, "failed_count": total_count},
            outputs={"input_dir": str(base_dir), "sheet_id": target_sheet_id, "sheet_name": target_sheet_name,
                     "union_id": union_id, "failed_files": failed_files},
        )

    outputs = {
        "input_dir": str(base_dir),
        "sheet_id": target_sheet_id,
        "sheet_name": target_sheet_name,
        "union_id": union_id,
        "failed_files": failed_files,
    }
    return JobResult(
        ok=True,
        message=f"上傳妮姬資料成功 ({ok_count}/{total_count})",
        stats={"ok_count": ok_count, "total_count": total_count, "failed_count": 0},
        outputs=outputs,
    )
