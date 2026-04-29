# -*- coding: utf-8 -*-
"""Upload healthcheck member list to the CharacterDetails spreadsheet."""

from __future__ import annotations

from typing import Any, Iterable

from gspread.utils import rowcol_to_a1

from ..data_store import build_healthcheck_upload_rows
from ..google_sheets.auth import get_gspread_client
from ..log import log_exc
from .common import JobResult
from .nikke_upload import resolve_character_sheet_for_account

HEALTHCHECK_WORKSHEET = "健檢名單"
HEALTHCHECK_HEADERS = ["名稱", "屬性", "類型", "暱稱", "優先度"]


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _pad_row(row: Iterable[Any], width: int) -> list[str]:
    values = [str(item or "") for item in row]
    if len(values) < width:
        values.extend([""] * (width - len(values)))
    return values[:width]


def _get_used_values(ws) -> list[list[str]]:
    return ws.get_all_values()


def _ensure_min_columns(ws, width: int) -> None:
    if ws.col_count < width:
        ws.add_cols(width - ws.col_count)


def _sync_healthcheck_sheet(spreadsheet, rows: list[dict[str, str]]) -> list[dict[str, str]]:
    ws = spreadsheet.worksheet(HEALTHCHECK_WORKSHEET)
    existing_values = _get_used_values(ws)
    width = max(max((len(row) for row in existing_values), default=0), len(HEALTHCHECK_HEADERS))
    _ensure_min_columns(ws, width)

    existing_rows = [_pad_row(row, width) for row in existing_values[1:]] if existing_values else []
    existing_by_name: dict[str, list[str]] = {}
    for row in existing_rows:
        name = _normalize_text(row[0])
        if name and name not in existing_by_name:
            existing_by_name[name] = row[:]

    output_rows: list[list[str]] = []
    synced_entries: list[dict[str, str]] = []
    for row in rows:
        name = _normalize_text(row.get("name"))
        element = _normalize_text(row.get("element"))
        member_type = _normalize_text(row.get("type"))
        full_row = existing_by_name.get(name, [""] * width)[:]
        while len(full_row) < width:
            full_row.append("")
        full_row[0] = name
        full_row[1] = element
        full_row[2] = member_type
        if not _normalize_text(full_row[3]):
            full_row[3] = name
        output_rows.append(full_row)
        synced_entries.append(
            {
                "name": name,
                "element": element,
                "type": member_type,
                "nickname": _normalize_text(full_row[3]) or name,
                "priority": _normalize_text(full_row[4]),
            }
        )

    header_range = f"A1:{rowcol_to_a1(1, len(HEALTHCHECK_HEADERS))}"
    ws.update(header_range, [HEALTHCHECK_HEADERS])
    if output_rows:
        ws.update(f"A2:{rowcol_to_a1(len(output_rows) + 1, width)}", output_rows)

    old_count = len(existing_rows)
    if old_count > len(output_rows):
        ws.delete_rows(len(output_rows) + 2, old_count + 1)
    elif not output_rows and ws.row_count > 1 and len(existing_values) > 1:
        ws.delete_rows(2, len(existing_values) - 1)

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
