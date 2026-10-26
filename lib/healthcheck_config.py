# -*- coding: utf-8 -*-
"""One sheet configuration shared by a download and its subsequent upload."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .data_store import (
    HEALTHCHECK_FUNCTION_KEYS,
    build_healthcheck_function_id_targets,
    normalize_healthcheck_rows,
    prepare_checklist_items,
)
from .google_sheets.healthcheck import (
    get_common_config_spreadsheet_id,
    healthcheck_rows_fingerprint,
    normalize_label,
    read_healthcheck_rows,
)

TRAIT_ABBREVIATIONS = {
    "優越代碼傷害": "優", "攻擊力": "攻", "最大裝彈數": "彈", "蓄力速度": "速",
    "暴擊率": "暴率", "暴擊傷害": "暴傷", "蓄力傷害": "蓄傷", "命中率": "命", "防禦力": "防",
}
TRAINING_PREFIX = "角色練度_"
CHANGE_WORKSHEET = "角色練度_變化"
BLOCK_WIDTH = 12


@dataclass(frozen=True)
class HealthcheckSnapshot:
    spreadsheet_id: str
    fingerprint: str
    members: list[dict[str, Any]]
    checklist_items: list[tuple[int, str]]
    function_id_targets: dict[str, list[int | None]]


def load_healthcheck_snapshot() -> HealthcheckSnapshot:
    rows = read_healthcheck_rows()
    members = normalize_healthcheck_rows(rows)
    is_valid, checklist, message = prepare_checklist_items(members)
    if not is_valid:
        raise ValueError(message)
    return HealthcheckSnapshot(
        get_common_config_spreadsheet_id(), healthcheck_rows_fingerprint(rows), members,
        checklist, build_healthcheck_function_id_targets(members),
    )


def ensure_healthcheck_snapshot_current(snapshot: HealthcheckSnapshot) -> None:
    if snapshot.spreadsheet_id != get_common_config_spreadsheet_id() or snapshot.fingerprint != healthcheck_rows_fingerprint(read_healthcheck_rows()):
        raise ValueError("共用健檢名單已變更，請重新下載練度資料後再上傳")


def validate_training_headers(
    spreadsheet, snapshot: HealthcheckSnapshot, *, worksheets=None, values_by_sheet=None,
) -> str:
    """Read only header rows and reject missing, obsolete, or misplaced role/trait blocks."""
    titles = {"角色練度_電擊", "角色練度_水冷", "角色練度_燃燒", "角色練度_風壓", "角色練度_鐵甲", CHANGE_WORKSHEET}
    worksheets = [ws for ws in (worksheets if worksheets is not None else spreadsheet.worksheets()) if ws.title in titles]
    if not worksheets:
        raise ValueError("目標試算表缺少角色練度工作表，請先在試算表執行健檢嚴格同步")
    ranges = [f"'{ws.title}'!1:{3 if ws.title == CHANGE_WORKSHEET else 2}" for ws in worksheets]
    if values_by_sheet is None:
        response = spreadsheet.values_batch_get(ranges)
        header_values = response.get("valueRanges", [])
    else:
        if any(ws.title not in values_by_sheet for ws in worksheets):
            raise ValueError("無法完整讀取角色練度表頭")
        header_values = [{"values": values_by_sheet[ws.title][:3 if ws.title == CHANGE_WORKSHEET else 2]} for ws in worksheets]
    if len(header_values) != len(worksheets):
        raise ValueError("無法完整讀取角色練度表頭")

    members_by_name = {normalize_label(item["name"]): item for item in snapshot.members}
    found_elements = set()
    fingerprint_rows = []
    for worksheet, result in zip(worksheets, header_values):
        is_change = worksheet.title == CHANGE_WORKSHEET
        header_row = 1 if is_change else 0
        values = result.get("values", [])
        element = worksheet.title[len(TRAINING_PREFIX):]
        expected = members_by_name if is_change else {
            key: item for key, item in members_by_name.items() if element in _member_elements(item)
        }
        if expected and len(values) <= header_row + 1:
            _raise_header_error(worksheet.title, "缺少角色或分類表頭")
        role_values = values[header_row] if len(values) > header_row else []
        group_values = values[header_row + 1] if len(values) > header_row + 1 else []
        if not is_change:
            found_elements.add(element)
        starts = [(index + 1, normalize_label(value)) for index, value in enumerate(role_values) if index >= 1 and normalize_label(value)]
        expected_keys = set(expected)
        actual_keys = {name for _, name in starts}
        if actual_keys != expected_keys or (not is_change and len(starts) != len(actual_keys)):
            missing = [item["name"] for key, item in expected.items() if key not in actual_keys]
            extra = [str(role_values[col - 1]) for col, key in starts if key not in expected_keys]
            _raise_header_error(worksheet.title, f"角色欄位不一致（缺少：{', '.join(missing) or '-'}；多餘：{', '.join(extra) or '-'}）")
        for index, (start_col, key) in enumerate(starts):
            if start_col != 2 + index * BLOCK_WIDTH:
                _raise_header_error(worksheet.title, f"{expected[key]['name']} 的角色欄位寬度或位置不符")
            item = expected[key]
            labels = [(0, "突破"), (1, "技能"), (4, "裝備 (頭/手/身/腳)"), (8, "收藏品"), (9, _trait_header(item))]
            for offset, label in labels:
                actual = group_values[start_col + offset - 1] if start_col + offset - 1 < len(group_values) else ""
                if normalize_label(actual) != normalize_label(label):
                    _raise_header_error(worksheet.title, f"{item['name']} 的「{label}」表頭不符（目前：{actual or '空白'}）")
            if worksheet.col_count < start_col + BLOCK_WIDTH - 1:
                _raise_header_error(worksheet.title, f"{item['name']} 的角色欄位不足 12 欄")
        fingerprint_rows.append([worksheet.title, [[normalize_label(value) for value in row] for row in values]])

    required_elements = {element for member in snapshot.members for element in _member_elements(member)}
    if required_elements - found_elements:
        _raise_header_error("目標試算表", f"缺少屬性表：{', '.join(sorted(required_elements - found_elements))}")
    return hashlib.sha256(json.dumps(fingerprint_rows, ensure_ascii=False).encode("utf-8")).hexdigest()


def build_download_context(snapshot: HealthcheckSnapshot, spreadsheet_id: str, header_fingerprint: str) -> dict[str, str]:
    return {
        "source_spreadsheet_id": snapshot.spreadsheet_id,
        "source_fingerprint": snapshot.fingerprint,
        "target_spreadsheet_id": spreadsheet_id,
        "target_header_fingerprint": header_fingerprint,
    }


def validate_download_context(
    document: dict[str, Any], snapshot: HealthcheckSnapshot, spreadsheet_id: str, header_fingerprint: str,
) -> None:
    expected = build_download_context(snapshot, spreadsheet_id, header_fingerprint)
    if document.get("healthcheck_context") != expected:
        raise ValueError("練度檔的健檢名單或目標表頭與目前設定不同，請重新下載後再上傳")
    members_by_code = {str(item["name_code"]): item for item in snapshot.members}
    details = document.get("character_details")
    if not isinstance(details, list) or not details:
        raise ValueError("練度檔缺少角色資料，請重新下載")
    expected_codes = document.get("expected_name_codes")
    actual_codes = [str(detail.get("name_code", "")) for detail in details if isinstance(detail, dict)]
    if (not isinstance(expected_codes, list) or len(actual_codes) != len(details)
            or len(actual_codes) != len(set(actual_codes))
            or len(expected_codes) != len({str(code) for code in expected_codes})
            or set(actual_codes) != {str(code) for code in expected_codes}):
        raise ValueError("練度檔缺少角色完整性資訊或角色資料不齊全，請重新下載")
    for detail in details:
        code = str(detail.get("name_code", "")) if isinstance(detail, dict) else ""
        member = members_by_code.get(code)
        if member is None or normalize_label(detail.get("name")) != normalize_label(member["name"]):
            raise ValueError("練度檔的角色與健檢名單不符，請重新下載")
        functions = detail.get("functions")
        if not isinstance(functions, list) or len(functions) != 3:
            raise ValueError(f"{member['name']} 的詞條資料不完整，請重新下載")
        actual_ids = [str(item.get("function_id") or "") if isinstance(item, dict) else "invalid" for item in functions]
        if actual_ids != [str(value or "") for value in snapshot.function_id_targets[code]]:
            raise ValueError(f"{member['name']} 的詞條順序與健檢名單不符，請重新下載")


def _member_elements(member: dict[str, Any]) -> list[str]:
    if normalize_label(member["name"]) == normalize_label("拉毗：小紅帽"):
        return ["燃燒", "鐵甲"]
    return [member["element"]]


def _trait_header(member: dict[str, Any]) -> str:
    parts = [TRAIT_ABBREVIATIONS.get(member.get(key), member.get(key)) for key in HEALTHCHECK_FUNCTION_KEYS if member.get(key)]
    return f"詞條 ({'/'.join(parts)})" if parts else "詞條"


def _raise_header_error(sheet_name: str, message: str) -> None:
    raise ValueError(f"{sheet_name}: {message}，請先在試算表執行健檢嚴格同步")
