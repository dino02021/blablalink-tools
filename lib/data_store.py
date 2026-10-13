# -*- coding: utf-8 -*-
"""Runtime settings, cloud dictionaries, and sheet-backed healthcheck selections."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Tuple

from .constants import (
    FUNCTION_NAMES_FILE,
    GOOGLE_SHEETS_ID_FILE,
    NIKKES_INFO_FILE,
    UNION_ACCOUNT_MAP_FILE,
    UNION_RAID_TYPE,
)
from .github_data_sync import read_cloud_data_json
from .google_sheets.healthcheck import normalize_label, read_healthcheck_rows, write_healthcheck_rows
from .github_secrets_sync import write_text_and_sync_if_changed
from .log import log_exc

HEALTHCHECK_TYPE_VALUES = ("輸出", "輔助")
HEALTHCHECK_FUNCTION_KEYS = ("function1", "function2", "function3")
ELEMENT_ZH_MAP = {
    "Fire": "燃燒",
    "Water": "水冷",
    "Iron": "鐵甲",
    "Wind": "風壓",
    "Electronic": "電擊",
}
_FUNCTION_NAME_REMOVE_RE = re.compile(r"[\s()\[\]{}<>（）［］｛｝＜＞【】「」『』《》〈〉]")


def load_sheets() -> Tuple[List[Dict[str, str]], List[str]]:
    """Read google_sheets_id.json and return (flat sheet rows, known types)."""
    sheets: List[Dict[str, str]] = []
    types: List[str] = []
    if not GOOGLE_SHEETS_ID_FILE.exists():
        return sheets, [UNION_RAID_TYPE]

    try:
        data = json.loads(GOOGLE_SHEETS_ID_FILE.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return sheets, [UNION_RAID_TYPE]

    if isinstance(data, list):
        for group in data:
            if not isinstance(group, dict):
                continue
            group_type = str(group.get("type", "") or "").strip()
            if group_type and group_type not in types:
                types.append(group_type)

            inner = group.get("sheets")
            if not isinstance(inner, list):
                continue
            for item in inner:
                if not isinstance(item, dict):
                    continue
                sheets.append(
                    {
                        "type": group_type,
                        "sheet_name": str(item.get("sheet_name", "") or "").strip(),
                        "sheet_id": str(item.get("sheet_id", "") or "").strip(),
                        "union_id": str(item.get("union_id", "") or "").strip(),
                    }
                )

    if not types:
        types = [UNION_RAID_TYPE]
    return sheets, types


def save_sheets(sheets: List[Dict[str, str]], sheet_types: List[str]) -> None:
    """Write google_sheets_id.json grouped by type."""
    try:
        GOOGLE_SHEETS_ID_FILE.parent.mkdir(parents=True, exist_ok=True)
        grouped: Dict[str, List[Dict[str, str]]] = {}
        for item in sheets:
            sheet_type = str(item.get("type", "") or "").strip()
            grouped.setdefault(sheet_type, []).append(
                {
                    "sheet_name": str(item.get("sheet_name", "") or "").strip(),
                    "sheet_id": str(item.get("sheet_id", "") or "").strip(),
                    "union_id": str(item.get("union_id", "") or "").strip(),
                }
            )

        ordered_types: List[str] = []
        for sheet_type in sheet_types:
            if sheet_type not in ordered_types:
                ordered_types.append(sheet_type)
        for sheet_type in grouped:
            if sheet_type not in ordered_types:
                ordered_types.append(sheet_type)

        payload = [{"type": sheet_type, "sheets": grouped.get(sheet_type, [])} for sheet_type in ordered_types]
        write_text_and_sync_if_changed(
            GOOGLE_SHEETS_ID_FILE,
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)


def load_union_account_map() -> List[Dict[str, str]]:
    """Read union_account_map.json."""
    if not UNION_ACCOUNT_MAP_FILE.exists():
        return []
    try:
        raw = json.loads(UNION_ACCOUNT_MAP_FILE.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return []

    if not isinstance(raw, list):
        return []

    cleaned: List[Dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        union_id = str(item.get("union_id", "")).strip()
        username = str(item.get("username", "")).strip()
        if not union_id or not username:
            continue
        cleaned.append({"union_id": union_id, "username": username})
    return cleaned


def save_union_account_map(items: List[Dict[str, str]]) -> None:
    """Write union_account_map.json."""
    try:
        payload: List[Dict[str, str]] = []
        for item in items:
            union_id = str(item.get("union_id", "")).strip()
            username = str(item.get("username", "")).strip()
            if not union_id or not username:
                continue
            payload.append({"union_id": union_id, "username": username})

        UNION_ACCOUNT_MAP_FILE.parent.mkdir(parents=True, exist_ok=True)
        write_text_and_sync_if_changed(
            UNION_ACCOUNT_MAP_FILE,
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)


def load_nikkes_info() -> List[Dict[str, Any]]:
    """Read nikkes_info.json and return normalized rows."""
    raw_info = read_cloud_data_json(NIKKES_INFO_FILE, default=[])
    if not isinstance(raw_info, list):
        return []

    result: List[Dict[str, Any]] = []
    for item in raw_info:
        if isinstance(item, dict):
            result.append(dict(item))
    return result


def _build_nikke_info_lookup() -> Dict[int, Dict[str, Any]]:
    lookup: Dict[int, Dict[str, Any]] = {}
    for item in load_nikkes_info():
        try:
            name_code = int(item.get("name_code"))
        except Exception:
            continue
        lookup[name_code] = item
    return lookup


def _build_name_lookup() -> Dict[int, str]:
    lookup: Dict[int, str] = {}
    for name_code, item in _build_nikke_info_lookup().items():
        name = str(item.get("name", "") or "").strip()
        if name:
            lookup[name_code] = name
    return lookup


def translate_nikke_element(raw_element: Any) -> str:
    text = str(raw_element or "").strip()
    if not text:
        return ""
    return ELEMENT_ZH_MAP.get(text, text)


def normalize_healthcheck_function_name(raw_name: Any) -> str:
    """Normalize cloud function names for healthcheck dropdown/storage."""
    text = _FUNCTION_NAME_REMOVE_RE.sub("", str(raw_name or ""))
    if text.endswith("增加"):
        text = text[: -len("增加")]
    return text.strip()


def load_healthcheck_function_names() -> List[str]:
    """Read normalized healthcheck function names from function_names.json."""
    return [item["name"] for item in load_healthcheck_function_definitions()]


def load_healthcheck_function_definitions() -> List[Dict[str, Any]]:
    """Read function_names.json with normalized names and ids."""
    raw_items = read_cloud_data_json(FUNCTION_NAMES_FILE, default=[])
    if not isinstance(raw_items, list):
        return []

    result: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        raw_name = str(item.get("function_name", "") or "").strip()
        name = normalize_healthcheck_function_name(raw_name)
        if not name or name in seen:
            continue
        function_id = item.get("function_id")
        try:
            function_id = int(function_id)
        except Exception:
            continue
        seen.add(name)
        result.append(
            {
                "function_name": raw_name,
                "name": name,
                "function_id": function_id,
            }
        )
    return result


def _match_healthcheck_function_id(selected_name: Any, definitions: List[Dict[str, Any]]) -> int | None:
    selected = str(selected_name or "").strip()
    selected_normalized = normalize_healthcheck_function_name(selected)
    if not selected and not selected_normalized:
        return None

    matches = {int(item["function_id"]) for item in definitions if selected_normalized == item["name"]}
    if len(matches) != 1:
        raise ValueError(f"健檢詞條無法唯一對應 function_id: {selected}")
    return matches.pop()


def build_healthcheck_function_id_targets(members: List[Dict[str, Any]] | None = None) -> Dict[str, List[int | None]]:
    """Return name_code -> function1/2/3 ids resolved from function_names.json."""
    definitions = load_healthcheck_function_definitions()
    result: Dict[str, List[int | None]] = {}
    for item in members if members is not None else load_healthcheck_members():
        code = str(item.get("name_code", "") or "").strip()
        targets = [
            _match_healthcheck_function_id(item.get(key), definitions)
            for key in HEALTHCHECK_FUNCTION_KEYS
        ]
        if code in result and result[code] != targets:
            raise ValueError(f"同一角色的輸出與輔助設定使用不同詞條: {item.get('name', code)}")
        result[code] = targets
    return result


def _normalize_healthcheck_member(
    item: Dict[str, Any],
    info_lookup: Dict[int, Dict[str, Any]],
) -> Dict[str, Any] | None:
    try:
        name_code = int(item.get("name_code"))
    except Exception:
        return None

    member_type = str(item.get("type", "") or "").strip()
    if member_type not in HEALTHCHECK_TYPE_VALUES:
        return None

    info = info_lookup.get(name_code, {})
    raw_element = str(item.get("element", "") or "").strip()
    element = translate_nikke_element(raw_element or info.get("element"))
    normalized = {
        "name_code": name_code,
        "name": str(item.get("name") or info.get("name") or "").strip(),
        "element": element,
        "type": member_type,
    }
    selected_functions: set[str] = set()
    for key in HEALTHCHECK_FUNCTION_KEYS:
        function_name = normalize_healthcheck_function_name(item.get(key))
        if not function_name or function_name in selected_functions:
            continue
        selected_functions.add(function_name)
        normalized[key] = function_name
    return normalized


def load_healthcheck_members() -> List[Dict[str, Any]]:
    """Read role selections and traits exclusively from the common spreadsheet."""
    return normalize_healthcheck_rows(read_healthcheck_rows())


def normalize_healthcheck_rows(raw_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    info_lookup = _build_nikke_info_lookup()
    name_to_codes: Dict[str, list[int]] = {}
    for code, info in info_lookup.items():
        name_to_codes.setdefault(normalize_label(info.get("name")), []).append(code)
    result: List[Dict[str, Any]] = []
    seen = set()
    for index, item in enumerate(raw_items, start=2):
        if str(item.get("element", "") or "").strip() not in ELEMENT_ZH_MAP.values():
            raise ValueError(f"健檢名單第 {index} 列的屬性無效")
        codes = name_to_codes.get(normalize_label(item.get("name")), [])
        if len(codes) != 1:
            raise ValueError(f"健檢名單第 {index} 列角色無法唯一對應 name_code: {item.get('name', '')}")
        normalized = _normalize_healthcheck_member({**item, "name_code": codes[0]}, info_lookup)
        if normalized is None or normalized["element"] not in ELEMENT_ZH_MAP.values():
            raise ValueError(f"健檢名單第 {index} 列的屬性或類型無效")
        key = (normalized["name_code"], normalized["type"])
        if key in seen:
            raise ValueError(f"健檢名單第 {index} 列角色與類型重複")
        seen.add(key)
        traits = [normalize_healthcheck_function_name(item.get(key)) for key in HEALTHCHECK_FUNCTION_KEYS]
        if len([trait for trait in traits if trait]) != len({trait for trait in traits if trait}):
            raise ValueError(f"健檢名單第 {index} 列的詞條重複")
        result.append(normalized)
    return result


def save_healthcheck_members(
    items: List[Dict[str, Any]],
    expected_members: List[Dict[str, Any]] | None = None,
) -> bool:
    """Edit the authoritative sheet and preserve its tracking/nickname/priority cells."""
    info_lookup = _build_nikke_info_lookup()
    normalized: List[Dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for item in items:
        normalized_item = _normalize_healthcheck_member(item, info_lookup)
        if normalized_item is None or not normalized_item["name"]:
            raise ValueError("健檢角色設定無效")
        key = (int(normalized_item["name_code"]), str(normalized_item["type"]))
        if key in seen:
            raise ValueError("該健檢角色與類型已存在")
        seen.add(key)
        normalized.append(normalized_item)

    build_healthcheck_function_id_targets(normalized)
    expected_rows = build_healthcheck_upload_rows(expected_members) if expected_members is not None else None
    write_healthcheck_rows(build_healthcheck_upload_rows(normalized), expected_rows=expected_rows)
    return True


def delete_healthcheck_member_by_index(index: int, expected_member: Dict[str, Any] | None = None) -> bool:
    """Delete one healthcheck member by the current list order."""
    items = load_healthcheck_members()
    if index < 0 or index >= len(items):
        return False
    if expected_member is not None and any(items[index].get(key, "") != expected_member.get(key, "") for key in ("name_code", "type", *HEALTHCHECK_FUNCTION_KEYS)):
        raise ValueError("健檢名單已變更，請重新讀取後再刪除")
    expected = [dict(item) for item in items]
    del items[index]
    return save_healthcheck_members(items, expected_members=expected)


def load_healthcheck_display_items() -> List[Dict[str, Any]]:
    """Return display rows for healthcheck list dialogs."""
    info_lookup = _build_nikke_info_lookup()
    result: List[Dict[str, Any]] = []
    for item in load_healthcheck_members():
        name_code = int(item["name_code"])
        info = info_lookup.get(name_code, {})
        name = str(item.get("name") or info.get("name") or "").strip() or f"未知角色({name_code})"
        element = str(item.get("element", "") or "").strip()
        member_type = str(item.get("type", "") or "").strip()
        row = {
            "name_code": name_code,
            "name": name,
            "element": element,
            "type": member_type,
            "display": f"{name} | {member_type}",
        }
        for key in HEALTHCHECK_FUNCTION_KEYS:
            function_name = str(item.get(key, "") or "").strip()
            if function_name:
                row[key] = function_name
        result.append(row)
    return result


def build_healthcheck_upload_rows(members: List[Dict[str, Any]] | None = None) -> List[Dict[str, str]]:
    """Return normalized rows for healthcheck sheet upload."""
    info_lookup = _build_nikke_info_lookup() if members is None or any(not item.get("name") for item in members) else {}
    rows: List[Dict[str, str]] = []
    for item in members if members is not None else load_healthcheck_members():
        name_code = int(item["name_code"])
        info = info_lookup.get(name_code, {})
        name = str(item.get("name") or info.get("name") or "").strip() or f"未知角色({name_code})"
        row = {
            "name": name,
            "element": str(item.get("element", "") or "").strip(),
            "type": str(item.get("type", "") or "").strip(),
        }
        for key in HEALTHCHECK_FUNCTION_KEYS:
            function_name = str(item.get(key, "") or "").strip()
            if function_name:
                row[key] = function_name
        rows.append(row)
    return rows


def prepare_checklist_items(members: List[Dict[str, Any]] | None = None) -> Tuple[bool, List[Tuple[int, str]], str]:
    """Load healthcheck members and map to unique checklist items."""
    raw_items = members if members is not None else load_healthcheck_members()
    if not raw_items:
        return False, [], "共用試算表的健檢名單沒有可用資料"

    name_lookup = _build_name_lookup() if any(not item.get("name") for item in raw_items) else {}
    seen_codes: set[int] = set()
    checklist_items: List[Tuple[int, str]] = []
    for item in raw_items:
        name_code_int = int(item["name_code"])
        if name_code_int in seen_codes:
            continue
        seen_codes.add(name_code_int)
        checklist_items.append((name_code_int, item.get("name") or name_lookup.get(name_code_int, "")))

    if not checklist_items:
        return False, [], "共用健檢名單沒有可用的角色代碼"
    return True, checklist_items, ""
