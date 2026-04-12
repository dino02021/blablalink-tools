# -*- coding: utf-8 -*-
"""資料讀寫：成員與試算表清單。"""

from __future__ import annotations

import json
from typing import Dict, List, Tuple

from .constants import (
    GOOGLE_SHEETS_ID_FILE,
    MEMBERS_ID_FILE,
    UNION_RAID_TYPE,
    NIKKE_CHECKLIST_NAME_CODES_FILE,
    NIKKES_INFO_FILE,
)
from .github_secrets_sync import write_text_and_sync_if_changed
from .log import log_exc


def load_members() -> List[Dict[str, str]]:
    """讀取 members_id.json，回傳成員清單。"""
    if MEMBERS_ID_FILE.exists():
        try:
            raw = json.loads(MEMBERS_ID_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                cleaned = []
                for item in raw:
                    if not isinstance(item, dict):
                        continue
                    cleaned.append(
                        {
                            "nick_name": str(item.get("nick_name", "")),
                            "intl_open_id": str(item.get("intl_open_id", "")),
                        },
                    )
                return cleaned
        except Exception as exc:  # noqa: BLE001
            log_exc(exc)
    return []


def save_members(members: List[Dict[str, str]]) -> None:
    """寫入 members_id.json。"""
    try:
        MEMBERS_ID_FILE.parent.mkdir(parents=True, exist_ok=True)
        write_text_and_sync_if_changed(
            MEMBERS_ID_FILE,
            json.dumps(members, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)


def load_sheets() -> Tuple[List[Dict[str, str]], List[str]]:
    """讀取 google_sheets_id.json，回傳 (sheets, types)。"""
    sheets: List[Dict[str, str]] = []
    types: List[str] = []
    if not GOOGLE_SHEETS_ID_FILE.exists():
        return sheets, [UNION_RAID_TYPE]
    try:
        data = json.loads(GOOGLE_SHEETS_ID_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            for grp in data:
                if not isinstance(grp, dict):
                    continue
                gtype = grp.get("type")
                if isinstance(gtype, str) and gtype not in types:
                    types.append(gtype)
                inner = grp.get("sheets")
                if not isinstance(inner, list):
                    continue
                for item in inner:
                    if not isinstance(item, dict):
                        continue
                    sheets.append(
                        {
                            "type": gtype or "",
                            "sheet_name": item.get("sheet_name", ""),
                            "sheet_id": item.get("sheet_id", ""),
                            "union_id": item.get("union_id", ""),
                        },
                    )
        if not types:
            types = [UNION_RAID_TYPE]
        return sheets, types
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return sheets, [UNION_RAID_TYPE]


def save_sheets(sheets: List[Dict[str, str]], sheet_types: List[str]) -> None:
    """寫入 google_sheets_id.json。"""
    try:
        GOOGLE_SHEETS_ID_FILE.parent.mkdir(parents=True, exist_ok=True)
        grouped: Dict[str, List[Dict[str, str]]] = {}
        for item in sheets:
            stype = item.get("type") or ""
            grouped.setdefault(stype, []).append(
                {
                    "sheet_name": item.get("sheet_name", ""),
                    "sheet_id": item.get("sheet_id", ""),
                    "union_id": item.get("union_id", ""),
                },
            )
        ordered_types: List[str] = []
        for stype in sheet_types:
            if stype not in ordered_types:
                ordered_types.append(stype)
        for stype in grouped.keys():
            if stype not in ordered_types:
                ordered_types.append(stype)
        payload = [{"type": stype, "sheets": grouped.get(stype, [])} for stype in ordered_types]
        write_text_and_sync_if_changed(
            GOOGLE_SHEETS_ID_FILE,
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)


def prepare_checklist_items() -> Tuple[bool, List[Tuple[int, str]], str]:
    """讀取 checklist name_code 並組合名稱。"""
    try:
        try:
            raw_codes = json.loads(NIKKE_CHECKLIST_NAME_CODES_FILE.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            log_exc(exc)
            raw_codes = None
        if not isinstance(raw_codes, list):
            return False, [], "nikke_checklist_name_codes.json 不存在或格式錯誤"

        name_lookup: dict[str, str] = {}
        try:
            raw_info = json.loads(NIKKES_INFO_FILE.read_text(encoding="utf-8"))
            if isinstance(raw_info, list):
                for itm in raw_info:
                    if not isinstance(itm, dict):
                        continue
                    nc = itm.get("name_code")
                    nm = itm.get("name")
                    if nc is None or nm is None:
                        continue
                    name_lookup[str(nc)] = str(nm)
        except Exception as exc:  # noqa: BLE001
            log_exc(exc)

        checklist_items: List[Tuple[int, str]] = []
        for nc in raw_codes:
            try:
                nc_int = int(nc)
            except Exception:
                continue
            nm = name_lookup.get(str(nc_int), "")
            checklist_items.append((nc_int, nm))
        if not checklist_items:
            return False, [], "nikke_checklist_name_codes.json 沒資料"
        return True, checklist_items, ""
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return False, [], str(exc)
