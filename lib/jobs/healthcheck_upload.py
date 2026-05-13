# -*- coding: utf-8 -*-
"""Upload healthcheck member list to the CharacterDetails spreadsheet."""

from __future__ import annotations

from typing import Any

from gspread.utils import rowcol_to_a1

from ..data_store import build_healthcheck_upload_rows
from ..google_sheets.auth import get_gspread_client
from ..log import log_exc
from .common import JobResult
from .nikke_upload import resolve_character_sheet_for_account

HEALTHCHECK_WORKSHEET = "健檢名單"
HEALTHCHECK_HEADERS = ["名稱", "屬性", "類型", "詞條1", "詞條2", "詞條3"]


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _get_used_values(ws) -> list[list[str]]:
    return ws.get_all_values()


def _ensure_min_columns(ws, width: int) -> None:
    if ws.col_count < width:
        ws.add_cols(width - ws.col_count)


def _sync_healthcheck_sheet(spreadsheet, rows: list[dict[str, str]]) -> list[dict[str, str]]:
    ws = spreadsheet.worksheet(HEALTHCHECK_WORKSHEET)
    existing_values = _get_used_values(ws)
    width = len(HEALTHCHECK_HEADERS)
    _ensure_min_columns(ws, width)

    output_rows: list[list[str]] = []
    synced_entries: list[dict[str, str]] = []
    for row in rows:
        name = _normalize_text(row.get("name"))
        element = _normalize_text(row.get("element"))
        member_type = _normalize_text(row.get("type"))
        function1 = _normalize_text(row.get("function1"))
        function2 = _normalize_text(row.get("function2"))
        function3 = _normalize_text(row.get("function3"))
        output_rows.append([name, element, member_type, function1, function2, function3])
        synced_entries.append(
            {
                "name": name,
                "element": element,
                "type": member_type,
                "function1": function1,
                "function2": function2,
                "function3": function3,
            }
        )

    header_range = f"A1:{rowcol_to_a1(1, len(HEALTHCHECK_HEADERS))}"
    ws.update(header_range, [HEALTHCHECK_HEADERS])
    if output_rows:
        ws.update(f"A2:{rowcol_to_a1(len(output_rows) + 1, width)}", output_rows)

    old_count = max(len(existing_values) - 1, 0)
    if old_count > len(output_rows):
        blank_rows = [[""] * width for _ in range(old_count - len(output_rows))]
        start_row = len(output_rows) + 2
        ws.update(f"A{start_row}:{rowcol_to_a1(old_count + 1, width)}", blank_rows)

    return synced_entries


def run_healthcheck_upload(account: str | None = None) -> JobResult:
    rows = build_healthcheck_upload_rows()
    if not rows:
        return JobResult(ok=False, message="nikke_healthcheck_members.json 沒有可上傳的資料")

    try:
        union_id, target_sheet_id, target_sheet_name = resolve_character_sheet_for_account(account)
    except ValueError as exc:
        return JobResult(ok=False, message=str(exc))

    outputs = {
        "union_id": union_id,
        "sheet_id": target_sheet_id,
        "sheet_name": target_sheet_name,
        "row_count": len(rows),
    }

    try:
        cli = get_gspread_client()
        spreadsheet = cli.open_by_key(target_sheet_id)
        _sync_healthcheck_sheet(spreadsheet, rows)
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return JobResult(ok=False, message=f"健檢名單同步失敗: {exc}", outputs=outputs)

    return JobResult(
        ok=True,
        message=f"健檢名單已同步 ({len(rows)})",
        outputs=outputs,
    )
