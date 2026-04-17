# -*- coding: utf-8 -*-
"""Helpers for runtime JSON-backed settings and cloud data."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from .constants import (
    GOOGLE_SHEETS_ID_FILE,
    NIKKE_HEALTHCHECK_MEMBERS_FILE,
    NIKKES_INFO_FILE,
    UNION_ACCOUNT_MAP_FILE,
    UNION_RAID_TYPE,
)
from .github_data_sync import read_cloud_data_json, write_data_text_and_sync_if_changed
from .github_secrets_sync import write_text_and_sync_if_changed
from .log import log_exc

HEALTHCHECK_TYPE_VALUES = ("輸出", "輔助")
ELEMENT_ZH_MAP = {
    "Fire": "燃燒",
    "Water": "水冷",
    "Iron": "鐵甲",
    "Wind": "風壓",
    "Electronic": "電擊",
}


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
    return {
        "name_code": name_code,
        "element": element,
        "type": member_type,
    }


def load_healthcheck_members() -> List[Dict[str, Any]]:
    """Read nikke_healthcheck_members.json from cloud data repo."""
    raw_items = read_cloud_data_json(NIKKE_HEALTHCHECK_MEMBERS_FILE, default=[])
    if not isinstance(raw_items, list):
        return []

    info_lookup = _build_nikke_info_lookup()
    result: List[Dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        normalized = _normalize_healthcheck_member(item, info_lookup)
        if normalized is None:
            continue
        result.append(normalized)
    return result


def save_healthcheck_members(items: List[Dict[str, Any]]) -> None:
    """Write nikke_healthcheck_members.json in normalized format."""
    info_lookup = _build_nikke_info_lookup()
    normalized: List[Dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        normalized_item = _normalize_healthcheck_member(item, info_lookup)
        if normalized_item is None:
            continue
        key = (int(normalized_item["name_code"]), str(normalized_item["type"]))
        if key in seen:
            continue
        seen.add(key)
        normalized.append(normalized_item)

    try:
        write_data_text_and_sync_if_changed(
            NIKKE_HEALTHCHECK_MEMBERS_FILE,
            json.dumps(normalized, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)


def load_healthcheck_display_items() -> List[Dict[str, Any]]:
    """Return display rows for healthcheck list dialogs."""
    info_lookup = _build_nikke_info_lookup()
    result: List[Dict[str, Any]] = []
    for item in load_healthcheck_members():
        name_code = int(item["name_code"])
        info = info_lookup.get(name_code, {})
        name = str(info.get("name", "") or "").strip() or f"未知角色({name_code})"
        element = str(item.get("element", "") or "").strip()
        member_type = str(item.get("type", "") or "").strip()
        result.append(
            {
                "name_code": name_code,
                "name": name,
                "element": element,
                "type": member_type,
                "display": f"{name} | {member_type}",
            }
        )
    return result


def build_healthcheck_upload_rows() -> List[Dict[str, str]]:
    """Return normalized rows for healthcheck sheet upload."""
    info_lookup = _build_nikke_info_lookup()
    rows: List[Dict[str, str]] = []
    for item in load_healthcheck_members():
        name_code = int(item["name_code"])
        info = info_lookup.get(name_code, {})
        name = str(info.get("name", "") or "").strip() or f"未知角色({name_code})"
        rows.append(
            {
                "name": name,
                "element": str(item.get("element", "") or "").strip(),
                "type": str(item.get("type", "") or "").strip(),
            }
        )
    return rows


def prepare_checklist_items() -> Tuple[bool, List[Tuple[int, str]], str]:
    """Load healthcheck members and map to unique checklist items."""
    raw_items = load_healthcheck_members()
    if not raw_items:
        return False, [], "nikke_healthcheck_members.json 格式錯誤、不存在或沒有可用資料"

    name_lookup = _build_name_lookup()
    seen_codes: set[int] = set()
    checklist_items: List[Tuple[int, str]] = []
    for item in raw_items:
        name_code_int = int(item["name_code"])
        if name_code_int in seen_codes:
            continue
        seen_codes.add(name_code_int)
        checklist_items.append((name_code_int, name_lookup.get(name_code_int, "")))

    if not checklist_items:
        return False, [], "nikke_healthcheck_members.json 沒有可用的角色代碼"
    return True, checklist_items, ""
