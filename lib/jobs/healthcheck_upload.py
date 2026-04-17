# -*- coding: utf-8 -*-
"""Upload healthcheck member list to the CharacterDetails spreadsheet."""

from __future__ import annotations

import json
from typing import Any, Dict, List
from urllib import error, request

from ..constants import WEB_APP_URL
from ..data_store import build_healthcheck_upload_rows
from ..google_sheets.auth import get_gspread_client
from ..log import log_exc
from .common import JobResult
from .nikke_upload import resolve_character_sheet_for_account

HEALTHCHECK_WORKSHEET = "健檢名單"
SCORE_RESULT_WORKSHEET = "練度評分結果"
HEALTHCHECK_TYPE_ORDER = ("輸出", "輔助")
HEALTHCHECK_ELEMENT_ORDER = ("電擊", "水冷", "燃燒", "風壓", "鐵甲")
TOTAL_LABELS = {"總分"}
RANK_LABELS = {"排名"}


def _post_healthcheck_sync(sheet_id: str, rows: list[Dict[str, str]]) -> tuple[bool, str]:
    if not WEB_APP_URL:
        return False, "WEB_APP_URL 未設定"

    payload = {
        "kind": "sync_healthcheck_members",
        "sheet_id": sheet_id,
        "rows": rows,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        WEB_APP_URL,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=600) as resp:
            body = resp.read().decode("utf-8", errors="replace").strip()
            ok = body.lower() == "ok"
            return ok, body or ("ok" if ok else "unknown_response")
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return False, body or f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return False, str(exc)


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_label(value: Any) -> str:
    return _normalize_text(value).replace(" ", "").replace("：", ":")


def _load_healthcheck_sheet_rows(sheet) -> list[dict[str, str]]:
    values = sheet.get_all_values()
    if len(values) < 2:
        return []

    rows: list[dict[str, str]] = []
    for raw_row in values[1:]:
        row = list(raw_row[:5])
        while len(row) < 5:
            row.append("")
        name = _normalize_text(row[0])
        element = _normalize_text(row[1])
        member_type = _normalize_text(row[2])
        nickname = _normalize_text(row[3])
        if not name and not element and not member_type and not nickname and not _normalize_text(row[4]):
            continue
        if not name or not element or not member_type:
            continue
        rows.append(
            {
                "name": name,
                "element": element,
                "type": member_type,
                "nickname": nickname or name,
            }
        )
    return rows


def _group_healthcheck_rows(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        key = f"{_normalize_text(row.get('type'))}\u0001{_normalize_text(row.get('element'))}"
        grouped.setdefault(key, []).append(row)
    return grouped


def _build_score_result_blocks(sheet) -> dict[str, dict[str, Any]]:
    values = sheet.get_all_values()
    if len(values) < 3:
        return {}

    row1 = values[0]
    row2 = values[1]
    row3 = values[2]
    width = max(len(row1), len(row2), len(row3))
    row1 += [""] * (width - len(row1))
    row2 += [""] * (width - len(row2))
    row3 += [""] * (width - len(row3))

    blocks: dict[str, dict[str, Any]] = {}
    current_type = ""
    current_element = ""
    for col in range(4, width + 1):
        idx = col - 1
        type_cell = _normalize_text(row1[idx])
        element_cell = _normalize_text(row2[idx])
        if type_cell:
            current_type = type_cell
        if element_cell:
            current_element = element_cell
        if not current_type or not current_element:
            continue

        key = f"{current_type}\u0001{current_element}"
        block = blocks.setdefault(
            key,
            {
                "type": current_type,
                "element": current_element,
                "char_cols": [],
                "total_col": None,
                "rank_col": None,
            },
        )

        label = _normalize_text(row3[idx])
        norm = _normalize_label(label)
        if norm in {_normalize_label(item) for item in TOTAL_LABELS}:
            block["total_col"] = col
            continue
        if norm in {_normalize_label(item) for item in RANK_LABELS}:
            block["rank_col"] = col
            continue
        block["char_cols"].append({"col": col, "label": label})
    return blocks


def _find_score_insert_col(block: dict[str, Any], desired_nickname: str, desired_rows: list[dict[str, str]]) -> int:
    desired_labels = [_normalize_text(item.get("nickname") or item.get("name") or "") for item in desired_rows]
    actual_by_norm = {
        _normalize_label(item.get("label")): int(item["col"])
        for item in block.get("char_cols", [])
        if _normalize_text(item.get("label"))
    }
    desired_idx = desired_labels.index(desired_nickname)
    for next_label in desired_labels[desired_idx + 1 :]:
        next_norm = _normalize_label(next_label)
        if next_norm in actual_by_norm:
            return actual_by_norm[next_norm]
    if block.get("total_col"):
        return int(block["total_col"])
    if block.get("rank_col"):
        return int(block["rank_col"])
    char_cols = block.get("char_cols", [])
    if char_cols:
        return int(char_cols[-1]["col"]) + 1
    raise ValueError(
        f"練度評分結果找不到可插入位置: type={block.get('type')} element={block.get('element')}"
    )


def _insert_score_result_column(sheet, insert_col: int, nickname: str) -> None:
    requests = [
        {
            "insertDimension": {
                "range": {
                    "sheetId": sheet.id,
                    "dimension": "COLUMNS",
                    "startIndex": insert_col - 1,
                    "endIndex": insert_col,
                },
                "inheritFromBefore": insert_col > 1,
            }
        }
    ]
    sheet.spreadsheet.batch_update({"requests": requests})
    sheet.update_cell(3, insert_col, nickname)


def _sync_score_result_columns(spreadsheet) -> int:
    healthcheck_ws = spreadsheet.worksheet(HEALTHCHECK_WORKSHEET)
    score_result_ws = spreadsheet.worksheet(SCORE_RESULT_WORKSHEET)

    desired_rows = _load_healthcheck_sheet_rows(healthcheck_ws)
    if not desired_rows:
        return 0

    desired_by_block = _group_healthcheck_rows(desired_rows)
    inserted = 0

    for member_type in HEALTHCHECK_TYPE_ORDER:
        for element in HEALTHCHECK_ELEMENT_ORDER:
            key = f"{member_type}\u0001{element}"
            wanted_rows = desired_by_block.get(key) or []
            if not wanted_rows:
                continue

            while True:
                blocks = _build_score_result_blocks(score_result_ws)
                block = blocks.get(key)
                if not block:
                    raise ValueError(f"練度評分結果找不到區塊: type={member_type} element={element}")

                actual_cols = block.get("char_cols", [])
                if len(actual_cols) >= len(wanted_rows):
                    break

                actual_norms = {_normalize_label(item.get("label")) for item in actual_cols}
                missing_row = None
                for row in wanted_rows:
                    nickname = _normalize_text(row.get("nickname") or row.get("name"))
                    if _normalize_label(nickname) not in actual_norms:
                        missing_row = row
                        break
                if missing_row is None:
                    break

                nickname = _normalize_text(missing_row.get("nickname") or missing_row.get("name"))
                insert_col = _find_score_insert_col(block, nickname, wanted_rows)
                _insert_score_result_column(score_result_ws, insert_col, nickname)
                inserted += 1

    return inserted


def run_healthcheck_upload(account: str | None = None) -> JobResult:
    rows = build_healthcheck_upload_rows()
    if not rows:
        return JobResult(ok=False, message="nikke_healthcheck_members.json 沒有可上傳的資料")

    try:
        union_id, target_sheet_id, target_sheet_name = resolve_character_sheet_for_account(account)
    except ValueError as exc:
        return JobResult(ok=False, message=str(exc))

    ok, message = _post_healthcheck_sync(target_sheet_id, rows)
    outputs = {
        "union_id": union_id,
        "sheet_id": target_sheet_id,
        "sheet_name": target_sheet_name,
        "row_count": len(rows),
    }
    if not ok:
        return JobResult(ok=False, message=message or "健檢名單同步失敗", outputs=outputs)

    inserted_score_columns = 0
    try:
        cli = get_gspread_client()
        spreadsheet = cli.open_by_key(target_sheet_id)
        inserted_score_columns = _sync_score_result_columns(spreadsheet)
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        outputs["score_result_inserted_columns"] = inserted_score_columns
        return JobResult(ok=False, message=f"健檢名單已同步，但練度評分結果補欄失敗: {exc}", outputs=outputs)

    outputs["score_result_inserted_columns"] = inserted_score_columns
    return JobResult(
        ok=True,
        message=f"健檢名單已同步 ({len(rows)})",
        outputs=outputs,
    )
