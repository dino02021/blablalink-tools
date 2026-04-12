# -*- coding: utf-8 -*-
"""Headless Nikke upload job."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from ..constants import CHAR_DETAILS_DIR, CHAR_DETAILS_TYPE
from ..data_store import load_sheets
from ..log import log_exc
from .common import JobResult


def _default_sheet_id() -> str:
    sheets, _ = load_sheets()
    matches = [item for item in sheets if item.get("type") == CHAR_DETAILS_TYPE]
    if not matches:
        return ""
    return str(matches[0].get("sheet_id", "") or "").strip()


def _resolve_files(files: List[str] | None, input_dir: str | Path | None) -> Tuple[Path, List[str]]:
    base_dir = Path(input_dir) if input_dir else CHAR_DETAILS_DIR
    base_dir.mkdir(parents=True, exist_ok=True)
    if files:
        return base_dir, [str(item).strip() for item in files if str(item).strip()]
    return base_dir, [item.name for item in sorted(base_dir.glob("*.json"))]


def run_nikke_upload(
    files: List[str] | None = None,
    input_dir: str | Path | None = None,
    sheet_id: str | None = None,
) -> JobResult:
    target_sheet_id = str(sheet_id or _default_sheet_id()).strip()
    if not target_sheet_id:
        return JobResult(ok=False, message="未找到 CharacterDetails 試算表")

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
            _load_change_headers,
            _load_sheet_headers,
            _notify_web_app_batch,
            _populate_change_updates,
        )
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return JobResult(ok=False, message=f"載入 nikke upload 核心失敗：{exc}")

    ok = False
    ok_count = 0
    total_count = len(target_files)
    msg = ""
    try:
        cli = get_gspread_client()
        sh = cli.open_by_key(target_sheet_id)

        worksheets = sh.worksheets()
        main_sheets = _collect_character_sheets(worksheets)
        if not main_sheets:
            return JobResult(ok=False, message="未找到角色練度工作表")
        change_sheet = _find_change_sheet(worksheets)
        change_nick_to_row, change_header_pos = _load_change_headers(change_sheet)

        nick_to_row, header_pos = _load_sheet_headers(main_sheets)
        if not nick_to_row:
            return JobResult(ok=False, message="讀取名單失敗")

        combined_updates: Dict[Any, List[Tuple[int, int, List[Any]]]] = {}
        combined_changes: Dict[Any, List[Tuple[int, int, List[Any]]]] = {}
        pending: List[Tuple[Any, int, int, List[Any], str, str]] = []
        for fname in target_files:
            data_file = base_dir / fname
            if not data_file.exists():
                msg = f"資料不存在: {fname}"
                continue
            try:
                obj = json.loads(data_file.read_text(encoding="utf-8"))
            except Exception:
                msg = f"讀取失敗: {fname}"
                continue
            nickname = obj.get("nickname", "")
            details = obj.get("character_details", [])
            if not nickname or not isinstance(details, list):
                msg = f"資料格式錯誤: {fname}"
                continue

            updates_by_ws, changes_by_ws = _build_updates(
                details,
                nickname,
                main_sheets,
                nick_to_row,
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

        _populate_change_updates(
            pending,
            combined_changes,
            change_sheet,
            change_nick_to_row,
            change_header_pos,
        )
        ok, notify_items = _apply_updates(combined_updates)
        if change_sheet and combined_changes:
            change_ok, change_notify_items = _apply_updates(combined_changes)
            ok = ok or change_ok
            notify_items.extend(change_notify_items)
        if notify_items:
            _notify_web_app_batch(notify_items)
        if not ok:
            msg = msg or "沒有任何資料可上傳"
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return JobResult(ok=False, message=str(exc))

    if not ok:
        return JobResult(
            ok=False,
            message=msg or "上傳妮姬資料失敗",
            stats={"ok_count": ok_count, "total_count": total_count},
            outputs={"input_dir": str(base_dir)},
        )

    return JobResult(
        ok=True,
        message=f"上傳妮姬資料成功 ({ok_count}/{total_count})",
        stats={"ok_count": ok_count, "total_count": total_count},
        outputs={"input_dir": str(base_dir)},
    )
