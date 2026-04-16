# -*- coding: utf-8 -*-
"""GUI 介面：LoginApp 負責登入流程、帳號管理與資料顯示。"""

import asyncio
import json
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Any, Optional, Dict

try:
    import ttkbootstrap as tb
except ImportError:
    tb = None

try:
    from screeninfo import get_monitors  # type: ignore
except Exception as exc:  # noqa: BLE001
    get_monitors = None
    try:
        from .log import log_exc

        log_exc(exc)
    except Exception:
        pass

from .log import log_msg, log_exc
from .github_data_sync import pull_cloud_data_files, start_cloud_data_watcher
from .github_secrets_sync import start_secret_file_watcher
from .utils import load_accounts, save_accounts, load_session, run_with_session_context
from .gui.ui_constants import DEFAULT_PAD
from .data_store import (
    load_sheets,
    load_union_account_map,
    save_sheets,
    save_union_account_map,
)
from .gui.dialogs.accounts import choose_or_manage_accounts
from .gui.dialogs.sheets import manage_sheet_types, manage_sheets
from .gui.dialogs.union_account_map import manage_union_account_map
from .gui.dialogs.scheduler_config import open_scheduler_config_dialog
from .gui.dialogs.union_raid_upload import open_upload_union_dialog
from .gui.union_raid_download import start_download_union
from .gui.nikke_download import start_download_nikke
from .gui.dialogs.nikke_batch_download import open_batch_download_nikke
from .gui.dialogs.nikke_upload import open_upload_nikke_dialog
from .gui.login_flow import start_login
from .api import fetch_function_names
from .jobs.sync_upload import run_sync_upload
from .constants import (
    LOG_PATH,
    SESSIONS_FILE,
    ACCOUNTS_FILE,
    NIKKES_INFO_FILE,
    FUNCTION_NAMES_FILE,
    GOOGLE_SHEETS_ID_FILE,
    CHAR_DETAILS_DIR,
    CHAR_DETAILS_TYPE,
    UNION_RAID_TEMP_FILE,
)


class LoginApp:
    def __init__(self) -> None:
        # 建立主視窗
        if tb:
            self.root = tb.Window(themename="cosmo")
        else:
            self.root = tk.Tk()
        self.root.title("Blablalink Login")
        self.root.geometry("480x560")
        self.root.resizable(True, True)

        # 欄位/狀態初始化
        self.username_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.status_var = tk.StringVar(value="")
        self.accounts_path = ACCOUNTS_FILE
        self.accounts = load_accounts()
        self.sheets, self.sheet_types = load_sheets()
        self.union_account_map = load_union_account_map()
        self._selected_account_index = None
        self._selected_sheet_index = None
        self._is_logged_in = False
        self._last_role_name = None
        self._last_role_id = None
        self._last_slug = None

        self._build_ui()

        # 確保相關目錄存在
        try:
            SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            NIKKES_INFO_FILE.parent.mkdir(parents=True, exist_ok=True)
            FUNCTION_NAMES_FILE.parent.mkdir(parents=True, exist_ok=True)
            GOOGLE_SHEETS_ID_FILE.parent.mkdir(parents=True, exist_ok=True)
            CHAR_DETAILS_DIR.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            log_exc(exc)

        try:
            pull_cloud_data_files()
        except Exception as exc:
            log_exc(exc)

        try:
            start_cloud_data_watcher()
        except Exception as exc:
            log_exc(exc)

        try:
            start_secret_file_watcher()
        except Exception as exc:
            log_exc(exc)

    def run(self) -> None:
        """啟動主視窗主迴圈。"""
        self.root.mainloop()

    # ------------------------------------------------------------
    # UI 建立
    # ------------------------------------------------------------
    def _build_ui(self) -> None:
        PAD = DEFAULT_PAD
        GAP = DEFAULT_PAD
        self._pad = PAD
        self._gap = GAP
        pad = PAD
        gap = GAP

        container = ttk.Frame(self.root, padding=pad)
        container.grid(row=0, column=0, sticky="nsew", padx=pad, pady=pad)
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)

        top = ttk.Frame(container)
        top.grid(row=0, column=0, sticky="nsew")
        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)
        self.top_frame = top
        top.columnconfigure(0, weight=1)
        top.columnconfigure(1, weight=1)

        # 登入表單
        form_frame = ttk.Frame(top, padding=(gap, gap, gap, gap))
        form_frame.grid(row=0, column=0, columnspan=2, sticky="ew", padx=(0, gap))
        form_frame.columnconfigure(1, weight=1)
        self.form_frame = form_frame

        ttk.Label(form_frame, text="帳號").grid(row=0, column=0, sticky="w", padx=(0, gap), pady=(0, gap // 2))
        self.username_entry = ttk.Entry(form_frame, textvariable=self.username_var)
        self.username_entry.grid(row=0, column=1, sticky="ew", pady=(0, gap // 2))

        ttk.Label(form_frame, text="密碼").grid(row=1, column=0, sticky="w", padx=(0, gap), pady=(0, gap // 2))
        self.password_entry = ttk.Entry(form_frame, textvariable=self.password_var, show="*")
        self.password_entry.grid(row=1, column=1, sticky="ew", pady=(0, gap // 2))

        btn_frame = ttk.Frame(form_frame, padding=(0, gap, 0, 0))
        btn_frame.grid(row=2, column=0, columnspan=2, sticky="e", pady=(gap, 0))
        btn_frame.columnconfigure(0, weight=1)
        self.manage_cloud_account_btn = ttk.Button(btn_frame, text="管理雲端帳號", command=self._open_manage_cloud_accounts)
        self.manage_cloud_account_btn.grid(row=0, column=0, padx=(0, gap))
        self.manage_sheet_btn = ttk.Button(btn_frame, text="管理試算表", command=self._open_manage_sheets)
        self.manage_sheet_btn.grid(row=0, column=1, padx=(0, gap))
        self.schedule_btn = ttk.Button(btn_frame, text="排程設定", command=self._open_scheduler_config)
        self.schedule_btn.grid(row=0, column=2, padx=(0, gap))
        self.manage_btn = ttk.Button(btn_frame, text="選擇/管理帳號", command=self._open_manage_accounts)
        self.manage_btn.grid(row=0, column=3, padx=(0, gap))
        self.login_btn = ttk.Button(btn_frame, text="登入", command=self._on_login_clicked)
        self.login_btn.grid(row=0, column=4)

        info_frame = ttk.Frame(top, padding=(gap, gap, gap, gap))
        info_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=(gap, gap), pady=(gap, gap))
        info_frame.columnconfigure(0, weight=0)
        info_frame.columnconfigure(1, weight=0)
        info_frame.columnconfigure(2, weight=1)  # 中間留彈性空間
        info_frame.columnconfigure(3, weight=0)
        info_frame.columnconfigure(4, weight=0)
        info_frame.columnconfigure(5, weight=0)
        self.info_frame = info_frame
        self.update_union_btn = ttk.Button(info_frame, text="更新聯盟突襲傷害表", command=self._on_update_union_raid)
        self.update_union_btn.grid(row=0, column=0, sticky="w", padx=(0, gap))
        self.manage_cloud_account_btn_logged = ttk.Button(
            info_frame,
            text="管理雲端帳號",
            command=self._open_manage_cloud_accounts,
        )
        self.manage_cloud_account_btn_logged.grid(row=0, column=3, sticky="e", padx=(0, gap))
        self.manage_sheet_btn_logged = ttk.Button(info_frame, text="管理試算表", command=self._open_manage_sheets)
        self.manage_sheet_btn_logged.grid(row=0, column=4, sticky="e", padx=(0, gap))
        self.logout_btn = ttk.Button(info_frame, text="登出", command=self._on_logout)
        self.logout_btn.grid(row=0, column=5, sticky="e", padx=(0, 0))
        self.union_status_var = tk.StringVar(value="")
        self.union_status_label = ttk.Label(info_frame, textvariable=self.union_status_var, foreground="green", anchor="w", justify="left")
        self.union_status_label.grid(row=3, column=0, columnspan=2, sticky="w", padx=(0, gap), pady=(gap // 2, 0))

        # 分隔線
        self.sep_nikke = ttk.Separator(top, orient="horizontal")
        self.sep_nikke.grid(row=1, column=0, columnspan=2, sticky="ew", padx=(0, gap), pady=(gap, gap))

        # 妮姬按鈕區塊
        nikke_frame = ttk.Frame(top, padding=(gap, gap * 2, gap, gap // 2))
        nikke_frame.grid(row=2, column=0, columnspan=2, sticky="ew")
        nikke_frame.columnconfigure(0, weight=0)
        nikke_frame.columnconfigure(1, weight=0)
        nikke_frame.columnconfigure(2, weight=0)
        nikke_frame.columnconfigure(3, weight=1)
        nikke_frame.columnconfigure(4, weight=0)
        self.nikke_frame = nikke_frame
        self.download_nikke_btn = ttk.Button(nikke_frame, text="下載本帳號妮姬資料", command=self._on_download_nikke)
        self.download_nikke_btn.grid(row=0, column=0, sticky="w", padx=(0, gap))
        self.batch_download_nikke_btn = ttk.Button(nikke_frame, text="批次下載妮姬資料", command=self._on_batch_download_nikke)
        self.batch_download_nikke_btn.grid(row=0, column=1, sticky="w", padx=(0, gap))
        self.upload_nikke_btn = ttk.Button(nikke_frame, text="上傳妮姬資料", command=self._on_upload_nikke)
        self.upload_nikke_btn.grid(row=0, column=2, sticky="w", padx=(0, gap))
        self.upload_sync_btn = ttk.Button(nikke_frame, text="上傳同步", command=self._on_upload_sync)
        self.upload_sync_btn.grid(row=0, column=4, sticky="e", padx=(0, 0))
        self.nikke_status_var = tk.StringVar(value="")
        self.nikke_status_label = ttk.Label(nikke_frame, textvariable=self.nikke_status_var, foreground="green", anchor="w", justify="left")
        self.nikke_status_label.grid(row=2, column=0, columnspan=2, sticky="w", padx=(0, gap), pady=(gap // 2, 0))
        self.logout_btn.grid_remove()
        self.manage_cloud_account_btn_logged.grid_remove()
        self.manage_sheet_btn_logged.grid_remove()
        self.update_union_btn.grid_remove()
        self.download_nikke_btn.grid_remove()
        self.batch_download_nikke_btn.grid_remove()
        self.upload_nikke_btn.grid_remove()
        self.upload_sync_btn.grid_remove()
        self.info_frame.grid_remove()
        self.sep_nikke.grid_remove()
        self.nikke_frame.grid_remove()

        self.status_label = ttk.Label(
            form_frame,
            textvariable=self.status_var,
            foreground="green",
            justify="right",
            anchor="e",
        )
        self.status_label.grid(row=3, column=0, columnspan=2, sticky="e", pady=(gap // 2, 0), padx=(0, gap))

        top.rowconfigure(5, weight=1)
        top.columnconfigure(0, weight=1)
        top.columnconfigure(1, weight=1)
        self._wrap_len = 360
        self.root.bind("<Configure>", self._on_resize)

        # 依登入狀態切換版面
        self._apply_layout(is_logged_in=False)
        self._update_title()

    # ------------------------------------------------------------
    # 標題/版面/調整
    # ------------------------------------------------------------
    def _update_title(self):
        base = "BlaBlaLink"
        if self._is_logged_in:
            parts = [base]
            if self._last_role_name:
                parts.append(f"- {self._last_role_name}")
            if self._last_role_id:
                parts.append(f"({self._last_role_id})")
            self.root.title(" ".join(parts))
        else:
            self.root.title("BlaBlaLink Login")

    def _apply_layout(self, is_logged_in: bool):
        gap = getattr(self, "_gap", 6)
        if is_logged_in:
            # 登入後隱藏表單
            self.form_frame.grid_remove()
            self.status_label.grid_remove()
            self.info_frame.grid(row=0, column=0, columnspan=2, sticky="ew", padx=(0, gap), pady=(gap, gap // 4))
            self.sep_nikke.grid(row=1, column=0, columnspan=2, sticky="ew", padx=(0, gap), pady=(0, gap // 6))
            self.nikke_frame.grid(row=2, column=0, columnspan=2, sticky="ew", padx=(0, gap), pady=(gap // 6, gap // 4))
            self.top_frame.rowconfigure(2, weight=0)
            self.top_frame.rowconfigure(3, weight=1)
        else:
            # 登出後顯示表單
            self.form_frame.grid(row=0, column=0, columnspan=2, sticky="ew", padx=(0, 6))
            self.status_label.grid(row=3, column=0, columnspan=2, sticky="e", pady=(3, 0), padx=(0, 6))
            self.info_frame.grid_remove()
            self.sep_nikke.grid_remove()
            self.nikke_frame.grid_remove()
            self.top_frame.rowconfigure(5, weight=1)

    def _on_resize(self, event):
        try:
            _ = event.width  # 只為觸發事件後續邏輯
        except Exception as exc:
            log_exc(exc)

    def _set_status(self, text: str, color: str = "green"):
        self.status_var.set(text)
        self.status_label.configure(foreground=color)

    def _set_nikke_status(self, text: str, color: str = "green"):
        self.nikke_status_var.set(text)
        self.nikke_status_label.configure(foreground=color)

    def _run_coro_sync(self, coro_factory):
        """
        在子執行緒同步執行 coroutine，避免主 loop 衝突。
        coro_factory: 回傳 coroutine 的 callable
        """
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(coro_factory())
            # 清理 async generators
            loop.run_until_complete(loop.shutdown_asyncgens())
            return result
        finally:
            asyncio.set_event_loop(None)
            loop.close()

    # ------------------------------------------------------------
    # 事件處理
    # ------------------------------------------------------------
    def _on_login_clicked(self):
        start_login(self)

    def _reset_login_buttons(self):
        self.login_btn.config(state="normal")
        self.manage_btn.config(state="normal")
        self.manage_cloud_account_btn.config(state="normal")
        self.manage_sheet_btn.config(state="normal")

    def _on_login_success(self):
        self.status_var.set("")
        self.manage_btn.grid_remove()
        self.login_btn.grid_remove()
        self.manage_cloud_account_btn.grid_remove()
        self.info_frame.grid()
        self.logout_btn.grid()
        self.update_union_btn.grid()
        self.nikke_frame.grid()
        self.download_nikke_btn.grid()
        self.batch_download_nikke_btn.grid()
        self.upload_nikke_btn.grid()
        self.upload_sync_btn.grid()
        self.manage_cloud_account_btn_logged.grid()
        self.manage_sheet_btn_logged.grid()
        self.manage_sheet_btn.grid_remove()
        self._set_nikke_status("", "green")
        self.union_status_var.set("")
        self._apply_layout(is_logged_in=True)

    def _trigger_fetch_function_names(self, username: str) -> None:
        """登入完成後異步取得 function_names，失敗不提示。"""
        if not username:
            return

        def runner():
            try:
                self._run_coro_sync(lambda: self._fetch_function_names_async(username))
            except Exception as exc:  # noqa: BLE001
                log_exc(exc)

        threading.Thread(target=runner, daemon=True).start()

    async def _fetch_function_names_async(self, username: str) -> None:
        state = load_session(username)
        if not state:
            return
        try:
            await run_with_session_context(state, fetch_function_names)
        except Exception as exc:  # noqa: BLE001
            log_exc(exc)
            return

    def _trigger_fetch_nikkes_info(self, username: str) -> None:
        """登入完成後異步取得 nikkes_info，失敗不提示。"""
        if not username:
            return

        def runner():
            try:
                self._run_coro_sync(lambda: self._fetch_nikkes_info_async(username))
            except Exception as exc:  # noqa: BLE001
                log_exc(exc)

        threading.Thread(target=runner, daemon=True).start()

    async def _fetch_nikkes_info_async(self, username: str) -> None:
        state = load_session(username)
        if not state:
            return
        try:
            from .api import fetch_nikkes_info
            await run_with_session_context(state, fetch_nikkes_info)
        except Exception as exc:  # noqa: BLE001
            log_exc(exc)
            return

    # ------------------------------------------------------------
    # 上傳妮姬 / 試算表 / 其他 UI
    # ------------------------------------------------------------
    def _on_download_union(self) -> None:
        start_download_union(self)

    def _on_update_union_raid(self) -> None:
        from .gui.dialogs.union_raid_update import open_union_raid_update_dialog
        result = open_union_raid_update_dialog(self.root)
        if result:
            mode = result.get("mode")
            month = result.get("month")
            from .gui.union_raid_update import start_union_raid_update
            start_union_raid_update(self, mode, month)

    def _on_download_nikke(self) -> None:
        start_download_nikke(self)

    def _on_batch_download_nikke(self) -> None:
        open_batch_download_nikke(self)

    def _on_upload_sheet(self) -> None:
        open_upload_union_dialog(self)

    def _on_upload_nikke(self) -> None:
        open_upload_nikke_dialog(self)

    def _open_manage_accounts(self) -> None:
        selected = choose_or_manage_accounts(self.root, self.accounts)
        self.accounts = load_accounts()
        if not selected:
            return
        self.username_var.set(selected.get("username", ""))
        self.password_var.set(selected.get("password", ""))
        self._selected_account_index = None

    def _open_manage_sheets(self) -> None:
        updated_sheets, _ = manage_sheets(self.root, self._gap, self.sheets, self.sheet_types)
        if updated_sheets is not None:
            self.sheets = updated_sheets
            save_sheets(self.sheets, self.sheet_types)

    def _open_manage_cloud_accounts(self) -> None:
        updated_items = manage_union_account_map(self.root, self.union_account_map)
        if updated_items is not None:
            self.union_account_map = updated_items
            save_union_account_map(self.union_account_map)

    def _open_scheduler_config(self) -> None:
        result = open_scheduler_config_dialog(self.root)
        if result:
            self._set_status(result.get("message", "排程已更新"), "green")

    def _on_logout(self) -> None:
        self._is_logged_in = False
        self._last_slug = None
        self._last_role_id = None
        self._last_role_name = None
        self._set_status("", "green")
        self._set_nikke_status("", "green")
        self.union_status_var.set("")
        self.root.geometry("480x560")
        self.username_var.set("")
        self.password_var.set("")
        self._apply_layout(is_logged_in=False)
        self.manage_btn.grid()
        self.manage_cloud_account_btn.grid()
        self.manage_sheet_btn.grid()
        self.login_btn.grid()
        self._reset_login_buttons()
        self.info_frame.grid_remove()
        self.sep_nikke.grid_remove()
        self.nikke_frame.grid_remove()
        self.update_union_btn.grid_remove()
        self.manage_cloud_account_btn_logged.grid_remove()
        self.manage_sheet_btn_logged.grid_remove()
        self.logout_btn.grid_remove()
        self.nikke_status_var.set("")
        self._update_title()

    def _on_upload_sync(self) -> None:
        if not self._is_logged_in or not self._last_slug:
            self._set_nikke_status("上傳同步失敗：尚未登入", "red")
            return
        self._set_nikke_status("上傳中...", "green")
        self.upload_sync_btn.config(state="disabled")
        threading.Thread(target=self._upload_sync_worker, daemon=True).start()

    def _upload_sync_worker(self) -> None:
        ok = False
        message = "上傳同步失敗：未知錯誤"
        try:
            result = run_sync_upload(account=self._last_slug or "")
            ok = result.ok
            message = result.message if result.ok else f"上傳同步失敗：{result.message}"
        except Exception as exc:  # noqa: BLE001
            log_exc(exc)
            message = f"上傳同步失敗：{exc}"
        finally:
            self.root.after(0, lambda: self._finish_upload_sync(ok, message))

    def _finish_upload_sync(self, ok: bool, message: str) -> None:
        self.upload_sync_btn.config(state="normal")
        self._set_nikke_status(message, "green" if ok else "red")
