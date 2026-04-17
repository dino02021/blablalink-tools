# -*- coding: utf-8 -*-
"""登入流程：點擊登入後的非同步處理與 UI 更新。"""

from __future__ import annotations

import functools
import threading
from typing import Tuple

from ..auth import run_playwright
from ..log import log_exc, log_msg


def start_login(app) -> None:
    """開始登入流程（由 GUI 事件呼叫）。"""
    username = app.username_var.get().strip()
    password = app.password_var.get().strip()
    if not username or not password:
        app._set_status("請輸入帳號密碼", "red")
        return

    app._set_status("登入中...", "green")
    app._set_busy(True)
    threading.Thread(target=_login_worker, args=(app, username, password), daemon=True).start()


def _login_worker(app, username: str, password: str) -> None:
    try:
        app._last_slug = username
        log_msg(f"login start: {username}")
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)

    try:
        coro_factory = functools.partial(run_playwright, username, password)
        result = app._run_coro_sync(coro_factory)
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        app.root.after(0, functools.partial(_finish_login_failed, app, f"登入失敗: {exc}"))
        return

    app.root.after(0, functools.partial(_handle_login_result, app, result))


def _handle_login_result(app, result: Tuple[str, list, str | None, str | None]) -> None:
    final_url, steps, uid_value, role_name = result
    log_msg(f"login finished steps={steps} uid={uid_value} role={role_name} url={final_url}")
    if uid_value:
        _finish_login_success(app, role_name, uid_value)
        return
    _finish_login_failed(app, "登入失敗")


def _finish_login_success(app, role_name: str | None, uid_value: str | None) -> None:
    app._last_role_name = role_name or app._last_role_name
    app._last_role_id = uid_value or app._last_role_id
    app._is_logged_in = True
    app._update_title()
    app._set_status("", "green")
    try:
        app._trigger_fetch_nikkes_info(app._last_slug or "")
        app._trigger_fetch_function_names(app._last_slug or "")
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
    app._on_login_success()


def _finish_login_failed(app, msg: str) -> None:
    app._set_status(msg, "red")
    app._reset_login_buttons()
