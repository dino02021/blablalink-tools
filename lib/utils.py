# -*- coding: utf-8 -*-
"""通用存取工具：帳號清單與 session cookies 的讀寫。"""

import base64
import json
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .constants import ACCOUNTS_FILE, DATA_DIR, PASS_KEY, SESSIONS_FILE
from .log import log_exc


def get_nested_value(obj: dict, *keys: str) -> Optional[Any]:
    """依序走訪多層 key，任一層不是 dict 或缺值時回傳 None。"""
    cur: Any = obj
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def col_letter(col: int) -> str:
    """將 1-based 欄位數字轉為 Excel 欄位字母。"""
    if col is None:
        raise TypeError("col is None")
    if col <= 0:
        return "A"
    result = ""
    n = col
    while n > 0:
        n -= 1
        result = chr(65 + (n % 26)) + result
        n //= 26
    return result


async def run_with_session_context(
    state: Dict[str, Any],
    handler: Callable[[Any], Awaitable[Any]],
) -> Any:
    """建立瀏覽器 context 後執行 handler，結束時自動清理。"""
    from playwright.async_api import async_playwright

    p = await async_playwright().start()
    browser = await p.chromium.launch(headless=True)
    ctx = await browser.new_context(storage_state=state)
    try:
        return await handler(ctx)
    finally:
        await browser.close()
        await p.stop()


def _xor_crypt(text: str, key: str) -> str:
    """以 XOR+Base64 做簡易遮罩（僅遮蔽，不具安全性）。"""
    kb = key.encode("utf-8")
    tb = text.encode("utf-8")
    out = bytes([tb[i] ^ kb[i % len(kb)] for i in range(len(tb))])
    return base64.urlsafe_b64encode(out).decode("utf-8")


def _xor_decrypt(token: str, key: str) -> str:
    """還原 XOR+Base64 遮罩內容，失敗則回傳原字串。"""
    try:
        data = base64.urlsafe_b64decode(token.encode("utf-8"))
        kb = key.encode("utf-8")
        out = bytes([data[i] ^ kb[i % len(kb)] for i in range(len(data))])
        return out.decode("utf-8")
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return token


def load_session(account: str) -> Optional[Dict[str, Any]]:
    """讀取 sessions.json，取得指定帳號的 storage_state（只保留 cookies）。"""
    if not SESSIONS_FILE.exists():
        return None
    try:
        data = json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return None

    if not isinstance(data, list):
        return None

    for item in data:
        if not isinstance(item, dict):
            continue
        if item.get("account", "") != account:
            continue
        cookies = item.get("cookies")
        if isinstance(cookies, list):
            return {"cookies": cookies}
    return None


def save_session(account: str, state_obj: Dict[str, Any]) -> None:
    """寫入 storage_state：同帳號會覆蓋舊資料，只存 cookies。"""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return

    data: list = []
    if SESSIONS_FILE.exists():
        try:
            loaded = json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                data = loaded
        except Exception as exc:  # noqa: BLE001
            log_exc(exc)
            data = []

    data = [
        item
        for item in data
        if not (isinstance(item, dict) and item.get("account", "") == account)
    ]

    cookies = state_obj.get("cookies", []) if isinstance(state_obj, dict) else []
    data.append({"account": account, "cookies": cookies})
    try:
        SESSIONS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)


def load_accounts() -> List[Dict[str, str]]:
    """讀取帳號清單並解密密碼。"""
    if not ACCOUNTS_FILE.exists():
        return []
    try:
        arr = json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return []

    if not isinstance(arr, list):
        return []

    res = []
    for item in arr:
        if not isinstance(item, dict):
            continue
        pwd = item.get("password", "")
        if pwd:
            pwd = _xor_decrypt(pwd, PASS_KEY)
        res.append(
            {
                "nickname": item.get("nickname", ""),
                "username": item.get("username", ""),
                "password": pwd,
            }
        )
    return res


def save_accounts(accounts: List[Dict[str, str]]) -> None:
    """儲存帳號資料，密碼以 XOR 混淆後寫入。"""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return

    data: List[Dict[str, str]] = []
    for item in accounts:
        pwd = item.get("password", "")
        enc = _xor_crypt(pwd, PASS_KEY) if pwd else ""
        data.append(
            {
                "nickname": item.get("nickname", ""),
                "username": item.get("username", ""),
                "password": enc,
            }
        )
    try:
        ACCOUNTS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
