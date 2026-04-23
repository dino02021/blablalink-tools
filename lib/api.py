# -*- coding: utf-8 -*-
"""封裝登入相關 API 呼叫。"""

import base64
import json
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any
from datetime import datetime

from playwright.async_api import APIRequestContext, BrowserContext

from .constants import (
    CHECKLOGIN_URL,
    ROLEINFO_URL,
    UNIONINFO_URL,
    UNION_RAID_URL,
    UNION_RAID_SEASON_URL,
    CHAR_DETAILS_URL,
    USER_CHARACTERS_URL,
    GUILDMEMBERS_URL,
    NIKKES_INFO_URL,
    FUNCTION_NAMES_URL,
    NIKKES_INFO_FILE,
    FUNCTION_NAMES_FILE,
    UNION_RAID_TEMP_FILE,
    CHAR_DETAILS_DIR,
)
from .github_data_sync import read_cloud_data_json, write_data_text_and_sync_if_changed
from .log import log_exc, log_msg
from .utils import get_nested_value


def build_character_details_path(nickname: str) -> Path:
    safe_name = str(nickname or "").strip()
    return CHAR_DETAILS_DIR / f"{safe_name}_character_details.json"


def _prefer_non_empty(new_value: Any, old_value: Any) -> Any:
    if new_value is None:
        return old_value
    if isinstance(new_value, str) and not new_value.strip():
        return old_value
    if isinstance(new_value, list) and not new_value:
        return old_value
    if isinstance(new_value, dict) and not new_value:
        return old_value
    return new_value


def _merge_nikkes_info_with_cloud(downloaded_items: List[dict]) -> List[dict]:
    existing_raw = read_cloud_data_json(NIKKES_INFO_FILE, default=[])
    merged: Dict[str, dict] = {}

    if isinstance(existing_raw, list):
        for item in existing_raw:
            if not isinstance(item, dict):
                continue
            key = str(item.get("name_code") or item.get("id") or "").strip()
            if not key:
                continue
            merged[key] = dict(item)

    for item in downloaded_items:
        key = str(item.get("name_code") or item.get("id") or "").strip()
        if not key:
            continue
        current = merged.get(key, {})
        next_item = dict(current)
        for field, value in item.items():
            next_item[field] = _prefer_non_empty(value, current.get(field))
        merged[key] = next_item

    return sorted(
        merged.values(),
        key=lambda item: (
            int(item.get("id")) if str(item.get("id", "")).isdigit() else 10**9,
            str(item.get("name_code") or ""),
            str(item.get("name") or ""),
        ),
    )


def _merge_function_names_with_cloud(downloaded_items: List[dict]) -> List[dict]:
    existing_raw = read_cloud_data_json(FUNCTION_NAMES_FILE, default=[])
    merged: Dict[str, List[int]] = {}

    if isinstance(existing_raw, list):
        for item in existing_raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("function_name") or "").strip()
            id_list = item.get("function_id_list")
            if not name or not isinstance(id_list, list):
                continue
            merged[name] = [value for value in id_list if isinstance(value, int)]

    for item in downloaded_items:
        name = str(item.get("function_name") or "").strip()
        id_list = item.get("function_id_list")
        if not name or not isinstance(id_list, list):
            continue
        existing = merged.setdefault(name, [])
        for value in id_list:
            if isinstance(value, int) and value not in existing:
                existing.append(value)

    return [
        {"function_name": name, "function_id_list": id_list}
        for name, id_list in sorted(merged.items(), key=lambda pair: pair[0])
    ]


async def check_login(context: BrowserContext) -> bool:
    """呼叫 CheckLogin API，回傳是否登入成功。"""
    try:
        req = context.request
        resp = await req.post(CHECKLOGIN_URL, data="{}")
        if resp.status != 200:
            return False

        try:
            data = await resp.json()
        except Exception as exc:  # noqa: BLE001
            log_exc(exc)
            return False

        if not isinstance(data, dict):
            return False

        return data.get("msg", "").lower() == "ok"
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return False


async def fetch_union_info(context: BrowserContext) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """呼叫公會資訊 API，回傳 (union_id, intl_open_id, nikke_area_id)，失敗則回傳 (None, None, None)。"""
    try:
        req = context.request
        resp = await req.post(UNIONINFO_URL, data={"ignore_toast": True})
        if resp.status != 200:
            return None, None, None
        try:
            data = await resp.json()
        except Exception as exc:  # noqa: BLE001
            log_exc(exc)
            return None, None, None

        if not isinstance(data, dict):
            return None, None, None

        card = data.get("data", {}).get("card", {}) if isinstance(data.get("data"), dict) else None
        if not isinstance(card, dict):
            return None, None, None

        union_id = card.get("guild_id")
        intl_open_id = _decode_intl_open_id(card.get("intl_open_id"))
        nikke_area_id = card.get("nikke_area_id")
        return union_id, intl_open_id, nikke_area_id
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return None, None, None


def _decode_intl_open_id(raw_value: str | None) -> str | None:
    if not raw_value:
        return None
    try:
        decoded = base64.b64decode(raw_value).decode("utf-8", errors="ignore")
    except Exception:
        return raw_value
    parts = decoded.split("-")
    if len(parts) < 2:
        return raw_value
    return parts[1]


def _build_union_raid_output(data: dict, union_id: str) -> dict | None:
    if not isinstance(data, dict):
        return None

    data_field = data.get("data")
    if not isinstance(data_field, dict):
        return None

    participate = data_field.get("participate_data")
    if not isinstance(participate, list):
        return None

    merged: Dict[str, Dict[str, Any]] = {}
    for item in participate:
        if not isinstance(item, dict):
            continue
        if item.get("day") == 0:
            continue
        username = item.get("nickname") or item.get("username")
        squad_raw = item.get("squad")
        if squad_raw is None:
            squad_raw = item.get("squad_id")
        if not isinstance(squad_raw, list):
            continue

        tid_list: List[Any] = []
        lv_val = None
        for elem in squad_raw:
            if not isinstance(elem, dict):
                continue
            tid = elem.get("tid")
            if tid is not None:
                tid_list.append(int(str(tid)[:4]))
            lv_candidate = elem.get("lv")
            if not isinstance(lv_candidate, (int, float)):
                continue
            if lv_val is None or lv_candidate > lv_val:
                lv_val = lv_candidate

        if not username:
            continue

        entry = {
            "damage": item.get("total_damage"),
            "level": item.get("level"),
            "step": item.get("step"),
            "is_final_hit": item.get("is_final_hit"),
            "squad_id": tid_list,
        }

        if username not in merged:
            merged[username] = {"username": username, "lv": lv_val, "data": [entry]}
            continue
        if lv_val is not None:
            prev_lv = merged[username].get("lv")
            if prev_lv is None or (isinstance(prev_lv, (int, float)) and lv_val > prev_lv):
                merged[username]["lv"] = lv_val
        merged[username]["data"].append(entry)

    if not merged:
        return None

    out_list = []
    for item in merged.values():
        if isinstance(item.get("data"), list):
            item["data"] = list(reversed(item["data"]))
        out_list.append(item)
    return {"union_id": union_id, "participate_data": out_list}


def _write_union_raid_output(wrapped: dict, union_id: str) -> bool:
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    out_path = UNION_RAID_TEMP_FILE.parent / f"{UNION_RAID_TEMP_FILE.stem}_{union_id}_{ts}{UNION_RAID_TEMP_FILE.suffix}"
    try:
        UNION_RAID_TEMP_FILE.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(wrapped, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return False
    return True


async def _fetch_and_store_union_raid_data(
    req: APIRequestContext,
    url: str,
    payload: dict[str, Any],
    union_id: str,
) -> bool:
    resp = await req.post(url, data=payload)
    if resp.status != 200:
        return False
    try:
        data = await resp.json()
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return False

    wrapped = _build_union_raid_output(data, union_id)
    if not wrapped:
        return False
    return _write_union_raid_output(wrapped, union_id)


async def fetch_union_raid_data(
    context: BrowserContext,
    union_id: str,
    intl_open_id: str,
    nikke_area_id: str,
) -> bool:
    """
    呼叫公會戰報 API，過濾/合併後寫入暫存檔，成功回傳 True，失敗回傳 False。

    處理規則：
    1) 只保留 participate_data 中 day != 0 的項目。
    2) 相同 nickname 合併，lv 取最高，data 依順序累加。
    3) squad.tid 依原順序存入列表（不強制長度檢查）。
    """
    try:
        req = context.request
        payload = {
            "guild_id": union_id,
            "intl_open_id": intl_open_id,
            "nikke_area_id": nikke_area_id,
        }
        return await _fetch_and_store_union_raid_data(req, UNION_RAID_URL, payload, union_id)
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return False


async def fetch_union_raid_data_of_guild_season(
    context: BrowserContext,
    area_id: str,
    guild_id: str,
    season_id: str,
) -> bool:
    """呼叫賽季版公會戰報 API，並使用與 fetch_union_raid_data 相同的處理與輸出格式。"""
    try:
        req = context.request
        payload = {
            "area_id": area_id,
            "guild_id": guild_id,
            "season_id": f"10000{str(season_id or '').strip()}",
        }
        return await _fetch_and_store_union_raid_data(req, UNION_RAID_SEASON_URL, payload, guild_id)
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return False


async def fetch_role_info(context: BrowserContext) -> Tuple[Optional[str], Optional[str]]:
    """呼叫 GetSavedRoleInfo，取得角色名稱與 ID。"""
    try:
        req = context.request
        resp = await req.get(ROLEINFO_URL)
        if resp.status != 200:
            return None, None

        try:
            data = await resp.json()
        except Exception as exc:  # noqa: BLE001
            log_exc(exc)
            return None, None

        if not isinstance(data, dict):
            return None, None
        
        if not isinstance(data.get("data"), dict):
            return None, None
        inner_data = data.get("data")
        
        if not isinstance(inner_data.get("role_info"), dict):
            return None, None
        role_info = inner_data.get("role_info")
        
        role_name = role_info.get("role_name")
        role_id = role_info.get("role_id")
        return role_name, role_id
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return None, None


async def fetch_nikkes_info(context: BrowserContext) -> bool:
    """以 GET 取得 NIKKE 資訊並直接寫入雲端 data repo，成功回傳 True，其他狀況回傳 False。"""
    try:
        req = context.request
        resp = await req.get(NIKKES_INFO_URL)
        if resp.status != 200:
            return False
        try:
            data = await resp.json()
        except Exception as exc:  # noqa: BLE001
            log_exc(exc)
            return False

        src_list: List[dict] = []
        if isinstance(data, list):
            src_list = [x for x in data if isinstance(x, dict)]
        else:
            return False

        result: List[dict] = []
        for item in src_list:
            mapped = {
                "id": int(str(item.get("id", ""))[:4]),
                "class": item.get("class"),
                "corporation": item.get("corporation"),
                "element": get_nested_value(item, "element_id", "element", "element"),
                "name_code": item.get("name_code"),
                "name": get_nested_value(item, "name_localkey", "name"),
                "original_rare": item.get("original_rare"),
                "weapon_type": get_nested_value(item, "shot_id", "element", "weapon_type"),
                "use_burst_skill": item.get("use_burst_skill"),
            }
            result.append(mapped)

        result = _merge_nikkes_info_with_cloud(result)
        if not result:
            return False

        try:
            write_data_text_and_sync_if_changed(
                NIKKES_INFO_FILE,
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001
            log_exc(exc)
            return False

        return True
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return False


async def fetch_user_character_details(
    context: BrowserContext,
    checklist_items: List[Tuple[str, str]],
    nickname: str,
    intl_open_id: str | None = None,
    nikke_area_id: str | None = None,
) -> bool:
    """
    取得角色詳細資訊並寫入暫存檔：
    - 參數 checklist_items: list of (name_code, name)
    - 檔名：{nickname}_character_details.json 儲存於 CHAR_DETAILS_DIR
    """
    try:
        target_nikke_area_id = str(nikke_area_id or "").strip()
        fetched_open_id: str | None = None
        if not target_nikke_area_id:
            _union_id, fetched_open_id, target_nikke_area_id = await fetch_union_info(context)
        intl_id = intl_open_id or fetched_open_id
        if not (intl_id and target_nikke_area_id):
            return False
        name_codes = [nc for nc, _ in checklist_items if nc]
        if not name_codes:
            return False
        name_map = {nc: nm for nc, nm in checklist_items if nc}
        # 去重複 name_code
        unique_codes = list(set(name_codes))

        payload = {
            "intl_open_id": intl_id,
            "nikke_area_id": target_nikke_area_id,
            "name_codes": unique_codes,
        }
        req = context.request
        resp = await req.post(CHAR_DETAILS_URL, data=payload)
        if resp.status != 200:
            return False
        try:
            data = await resp.json()
        except Exception as exc:  # noqa: BLE001
            log_exc(exc)
            return False
        
        if not isinstance(data, dict):
            return False
        data_field = data.get("data")
        if not isinstance(data_field, dict):
            return False

        # 預先整理 state_effects -> se_id(前7碼) -> function_value (轉百分比)
        state_effects = data_field.get("state_effects")
        func_value_map: dict[str, float] = {}
        if not isinstance(state_effects, list):
            return False
        for itm in state_effects:
            if not isinstance(itm, dict):
                continue
            fid = str(itm.get("id", ""))
            fd_list = itm.get("function_details")
            if not isinstance(fd_list, list) or not fd_list:
                continue
            fd = fd_list[0]
            if not isinstance(fd, dict):
                continue
            try:
                val = float(fd.get("function_value", 0)) / 100.0
            except Exception:
                continue
            func_value_map[fid] = val

        results = []
        targets = ["70005", "70008", "70007"]
        
        cd = data_field.get("character_details")
        if not isinstance(cd, list):
            return False
        
        for itm in cd:
            name_code = itm.get("name_code")
            name = name_map.get(name_code, "")
            fav_raw = str(itm.get("favorite_item_tid", "") or "")
            fav_first = fav_raw[0] if fav_raw else ""
            fav_last = fav_raw[-1] if fav_raw else ""
            if fav_first == "2":
                fav_type = "SR"
                fav_lv = 15
            else:
                fav_type = {"0": "N", "1": "R", "2": "SR"}.get(fav_last, fav_raw)
                fav_lv = itm.get("favorite_item_lv")

            # 掃描 equip_option* 找到對應 function_id（欄位為單一數值）
            func_sum: dict[str, float] = {}
            for fid in targets:
                func_sum[fid] = 0.0
                matched_val = None
                for k, v in itm.items():
                    if "equip_option" not in k:
                        continue
                    opt_val = str(v)
                    if opt_val[:5] != fid:
                        continue
                    if opt_val in func_value_map:
                        matched_val = func_value_map[opt_val]
                        if matched_val is not None:
                            func_sum[fid] = func_sum.get(fid, 0.0) + matched_val
            functions = [{"function_id": fid, "function_value": round(val, 2)} for fid, val in func_sum.items()]

            results.append(
                {
                    "name": name,
                    "skill1_lv": itm.get("skill1_lv"),
                    "skill2_lv": itm.get("skill2_lv"),
                    "ulti_skill_lv": itm.get("ulti_skill_lv"),
                    "head_equip_tier": itm.get("head_equip_tier"),
                    "head_equip_lv": itm.get("head_equip_lv"),
                    "arm_equip_tier": itm.get("arm_equip_tier"),
                    "arm_equip_lv": itm.get("arm_equip_lv"),
                    "torso_equip_tier": itm.get("torso_equip_tier"),
                    "torso_equip_lv": itm.get("torso_equip_lv"),
                    "leg_equip_tier": itm.get("leg_equip_tier"),
                    "leg_equip_lv": itm.get("leg_equip_lv"),
                    "favorite_item_type": fav_type,
                    "favorite_item_lv": fav_lv,
                    "functions": functions,
                }
            )

        if not results:
            return False

        try:
            CHAR_DETAILS_DIR.mkdir(parents=True, exist_ok=True)
            outfile = build_character_details_path(nickname)
            outfile.write_text(
                json.dumps({"nickname": nickname, "character_details": results}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return True
        except Exception as exc:  # noqa: BLE001
            log_exc(exc)
            return False
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return False


async def fetch_user_characters(
    context: BrowserContext,
    intl_open_id: str | None = None,
    nikke_area_id: str | None = None,
) -> List[Any]:
    """呼叫 GetUserCharacters，回傳 name_code 清單。"""
    try:
        target_nikke_area_id = str(nikke_area_id or "").strip()
        fetched_open_id: str | None = None
        if not target_nikke_area_id:
            _union_id, fetched_open_id, target_nikke_area_id = await fetch_union_info(context)
        if not target_nikke_area_id:
            return []
        intl_id = intl_open_id or fetched_open_id
        if not intl_id:
            return []
        payload = {
            "intl_open_id": intl_id,
            "nikke_area_id": target_nikke_area_id,
        }
        req = context.request
        resp = await req.post(USER_CHARACTERS_URL, data=payload)
        if resp.status != 200:
            return []
        try:
            data = await resp.json()
        except Exception as exc:  # noqa: BLE001
            log_exc(exc)
            return []
        if not isinstance(data, dict):
            return []
        data_field = data.get("data")
        if not isinstance(data_field, dict):
            return []
        characters = data_field.get("characters")
        if not isinstance(characters, list):
            return []
        name_codes: List[Any] = []
        for item in characters:
            if not isinstance(item, dict):
                continue
            name_code = item.get("name_code")
            if name_code is None:
                continue
            name_codes.append(name_code)
        return name_codes
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return []


async def fetch_guild_members(
    context: BrowserContext,
    guild_id: str | None = None,
    nikke_area_id: str | None = None,
) -> Tuple[str, List[Dict[str, Any]]]:
    """呼叫 GetGuildMembers，回傳 (guild_id, 成員清單)。"""
    try:
        target_guild_id = str(guild_id or "").strip()
        target_nikke_area_id = str(nikke_area_id or "").strip()
        if not target_guild_id or not target_nikke_area_id:
            target_guild_id, _intl_open_id, target_nikke_area_id = await fetch_union_info(context)
        if not target_guild_id or not target_nikke_area_id:
            return "", []
        payload = {
            "guild_id": target_guild_id,
            "nikke_area_id": target_nikke_area_id,
        }
        req = context.request
        resp = await req.post(GUILDMEMBERS_URL, data=payload)
        if resp.status != 200:
            return "", []

        try:
            data = await resp.json()
        except Exception as exc:  # noqa: BLE001
            log_exc(exc)
            return "", []

        if not isinstance(data, dict):
            return "", []

        data_field = data.get("data")
        if not isinstance(data_field, dict):
            return "", []

        items = data_field.get("items")
        if not isinstance(items, list):
            return "", []

        result: List[Dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            result.append(
                {
                    "nickname": item.get("nickname"),
                    "member_id": item.get("member_id"),
                    "synchro_level": item.get("synchro_level"),
                }
            )
        return target_guild_id, result
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return "", []


async def fetch_function_names(context: BrowserContext) -> bool:
    """以 GET 取得 function_names 資料，依需求合併與過濾後寫入檔案，成功回傳 True，其他狀況回傳 False。"""
    try:
        req = context.request
        resp = await req.get(FUNCTION_NAMES_URL)
        if resp.status != 200:
            return False
        try:
            data = await resp.json()
        except Exception as exc:  # noqa: BLE001
            log_exc(exc)
            return False

        if not isinstance(data, list):
            return False

        merged: dict[str, List[int]] = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            desc = item.get("description_localkey")
            ids = item.get("state_effect_id_list")
            if not desc or not isinstance(desc, str):
                continue
            if not isinstance(ids, list) or not ids:
                continue
            first = ids[0]
            first_str = str(first)
            if not first_str.startswith("700"):
                continue
            existing = merged.setdefault(desc, [])
            for val in ids:
                if val not in existing:
                    existing.append(val)

        result = [
            {"function_name": desc, "function_id_list": id_list}
            for desc, id_list in merged.items()
        ]
        result = _merge_function_names_with_cloud(result)
        if not result:
            return False

        try:
            write_data_text_and_sync_if_changed(
                FUNCTION_NAMES_FILE,
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001
            log_exc(exc)
            return False

        return True
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return False

