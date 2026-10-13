# -*- coding: utf-8 -*-
"""Check a union's layout against the authoritative common healthcheck sheet."""

from __future__ import annotations

from ..google_sheets.auth import get_gspread_client
from ..healthcheck_config import (
    ensure_healthcheck_snapshot_current,
    load_healthcheck_snapshot,
    validate_training_headers,
)
from .common import JobResult
from .nikke_upload import resolve_character_sheet_for_account

def run_healthcheck_upload(account: str | None = None) -> JobResult:
    """Keep the entry point; configuration edits now save directly to Sheets."""
    try:
        snapshot = load_healthcheck_snapshot()
        union_id, target_sheet_id, target_sheet_name = resolve_character_sheet_for_account(account)
        spreadsheet = get_gspread_client().open_by_key(target_sheet_id)
        validate_training_headers(spreadsheet, snapshot)
        ensure_healthcheck_snapshot_current(snapshot)
    except Exception as exc:
        return JobResult(ok=False, message=str(exc))
    return JobResult(
        ok=True,
        message=f"角色與詞條欄位已同步 ({len(snapshot.checklist_items)} 位角色)",
        outputs={
            "union_id": union_id,
            "sheet_id": target_sheet_id,
            "sheet_name": target_sheet_name,
            "source_sheet_id": snapshot.spreadsheet_id,
        },
    )
