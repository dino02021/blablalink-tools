# -*- coding: utf-8 -*-
"""Helpers for runtime JSON-backed settings and lookup data."""

from __future__ import annotations

import json
from typing import Dict, List, Tuple

from .constants import (
    GOOGLE_SHEETS_ID_FILE,
    NIKKE_CHECKLIST_NAME_CODES_FILE,
    NIKKES_INFO_FILE,
    UNION_ACCOUNT_MAP_FILE,
    UNION_RAID_TYPE,
)
from .github_secrets_sync import write_text_and_sync_if_changed
from .log import log_exc


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


def prepare_checklist_items() -> Tuple[bool, List[Tuple[int, str]], str]:
    """Load checklist name_code values and map them to Nikke names."""
    try:
        raw_codes = json.loads(NIKKE_CHECKLIST_NAME_CODES_FILE.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        raw_codes = None

    if not isinstance(raw_codes, list):
        return False, [], "nikke_checklist_name_codes.json 格式錯誤或不存在"

    name_lookup: Dict[str, str] = {}
    try:
        raw_info = json.loads(NIKKES_INFO_FILE.read_text(encoding="utf-8"))
        if isinstance(raw_info, list):
            for item in raw_info:
                if not isinstance(item, dict):
                    continue
                name_code = item.get("name_code")
                name = item.get("name")
                if name_code is None or name is None:
                    continue
                name_lookup[str(name_code)] = str(name)
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)

    checklist_items: List[Tuple[int, str]] = []
    for raw_code in raw_codes:
        try:
            name_code_int = int(raw_code)
        except Exception:
            continue
        checklist_items.append((name_code_int, name_lookup.get(str(name_code_int), "")))

    if not checklist_items:
        return False, [], "nikke_checklist_name_codes.json 沒有可用的角色代碼"
    return True, checklist_items, ""
