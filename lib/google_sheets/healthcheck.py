# -*- coding: utf-8 -*-
"""The common spreadsheet is the authoritative healthcheck member list."""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from typing import Any

from .auth import get_gspread_client

DEFAULT_COMMON_CONFIG_SPREADSHEET_ID = "167RcdRZu8OY5Im9wqWHDBUjZAiENJvv7mvFD74ZXzgU"
HEALTHCHECK_WORKSHEET = "健檢名單"
HEALTHCHECK_HEADERS = ["名稱", "屬性", "類型", "詞條1", "詞條2", "詞條3"]
HEALTHCHECK_KEYS = ("name", "element", "type", "function1", "function2", "function3")


def get_common_config_spreadsheet_id() -> str:
    return os.getenv("BLABLALINK_COMMON_CONFIG_SPREADSHEET_ID", "").strip() or DEFAULT_COMMON_CONFIG_SPREADSHEET_ID


def read_healthcheck_rows() -> list[dict[str, str]]:
    worksheet = _open_healthcheck_worksheet()
    return _parse_rows(worksheet.get("A1:F"))


def healthcheck_rows_fingerprint(rows: list[dict[str, Any]]) -> str:
    values = [[_fingerprint_value(row.get(key), key) for key in HEALTHCHECK_KEYS] for row in rows]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode("utf-8")).hexdigest()


def write_healthcheck_rows(
    rows: list[dict[str, str]],
    expected_rows: list[dict[str, str]] | None = None,
) -> None:
    """Update A-F in place; deleting a member moves its extra cells with its row."""
    worksheet = _open_healthcheck_worksheet()
    values = worksheet.get("A1:F")
    current = _parse_rows(values)
    if expected_rows is not None and healthcheck_rows_fingerprint(current) != healthcheck_rows_fingerprint(expected_rows):
        raise ValueError("共用健檢名單已變更，請重新讀取後再儲存")

    existing = [(index, _row_dict(row)) for index, row in enumerate(values[1:], start=2) if any(_text(v) for v in row[:6])]
    available = dict(existing)
    wanted_keys = {_member_key(row) for row in rows}
    updates = []
    additions = []
    for row in rows:
        key = _member_key(row)
        matches = [index for index, item in available.items() if _member_key(item) == key]
        if not matches:
            # A type change still belongs to the same role's tracking/nickname/priority cells.
            matches = [index for index, item in available.items() if normalize_label(item["name"]) == key[0] and _member_key(item) not in wanted_keys]
            if len(matches) != 1:
                matches = []
        output = [_text(row.get(column)) for column in HEALTHCHECK_KEYS]
        if not matches:
            additions.append(output)
            continue
        index = matches[0]
        item = available.pop(index)
        if output != [_text(item.get(column)) for column in HEALTHCHECK_KEYS]:
            updates.append({"range": f"A{index}:F{index}", "values": [output]})

    if updates:
        worksheet.batch_update(updates, value_input_option="RAW")
    for index in sorted(available, reverse=True):
        worksheet.delete_rows(index)
    if additions:
        worksheet.append_rows(additions, value_input_option="RAW", insert_data_option="INSERT_ROWS", table_range="A:F")


def normalize_label(value: Any) -> str:
    text = unicodedata.normalize("NFKC", _text(value)).replace("：", ":")
    for char in ("\u200b", "\u200c", "\u200d", "\ufeff"):
        text = text.replace(char, "")
    return "".join(text.split())


def _open_healthcheck_worksheet():
    try:
        return get_gspread_client().open_by_key(get_common_config_spreadsheet_id()).worksheet(HEALTHCHECK_WORKSHEET)
    except Exception as exc:
        raise ValueError(f"無法讀取共用試算表的健檢名單: {exc}") from exc


def _parse_rows(values: list[list[Any]]) -> list[dict[str, str]]:
    if not values or [_text(value) for value in values[0][:6]] != HEALTHCHECK_HEADERS:
        raise ValueError("共用健檢名單 A-F 表頭必須是：名稱、屬性、類型、詞條1、詞條2、詞條3")
    return [_row_dict(row) for row in values[1:] if any(_text(value) for value in row[:6])]


def _row_dict(values: list[Any]) -> dict[str, str]:
    return {key: _text(values[index] if index < len(values) else "") for index, key in enumerate(HEALTHCHECK_KEYS)}


def _member_key(row: dict[str, Any]) -> tuple[str, str]:
    return normalize_label(row.get("name")), _text(row.get("type"))


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _fingerprint_value(value: Any, key: str) -> str:
    text = normalize_label(value)
    if key.startswith("function"):
        text = re.sub(r"[()\[\]{}<>【】「」『』《》〈〉]", "", text)
        text = text.removesuffix("增加")
    return text
