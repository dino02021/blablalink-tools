# -*- coding: utf-8 -*-
"""排程設定對話框：修改 union_raid_pipeline 的執行日期和月份。"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import threading
import tkinter as tk
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, Dict, Optional
from urllib import error, request

from ..layout import (
    close_dialog,
    create_dialog,
    create_frame,
    create_entry_label,
    create_entry,
    create_entry_frame,
    create_btn_frame,
    create_btn_between,
    create_btn_last,
    create_msg_label,
)
from ...constants import PROJECT_ROOT
from ...log import log_exc, log_msg


SCHEDULER_CONFIG_PATH = Path(__file__).parent.parent.parent.parent / ".github" / "runtime" / "scheduler_config.json"


def open_scheduler_config_dialog(parent: tk.Misc) -> Optional[Dict[str, Any]]:
    """開啟排程設定對話框。"""
    dlg = create_dialog(parent, "排程設定", 360, 170)

    frame = create_frame(dlg)
    frame.columnconfigure(0, weight=1)
    
    # 使用本地預設值，避免對話框出現延遲
    current_date, current_month = _get_default_date(), _get_default_month(_get_default_date())

    # 日期與月份輸入框
    entry_frame = create_entry_frame(frame, row=0, column=0)
    entry_frame.columnconfigure(1, weight=1)
    
    create_entry_label(entry_frame, "執行日期 (YYYY-MM-DD):", row=0, column=0)
    date_var = tk.StringVar(value=current_date)
    create_entry(entry_frame, date_var, row=0, column=1, sticky="ew")

    create_entry_label(entry_frame, "上傳月份 (YYYY/MM):", row=1, column=0)
    month_var = tk.StringVar(value=current_month)
    create_entry(entry_frame, month_var, row=1, column=1, sticky="ew")

    # 用 flag 控制是否執行自動月份更新
    is_updating_from_cloud = tk.BooleanVar(value=False)
    
    def _on_date_change_with_flag(*args: Any) -> None:
        if not is_updating_from_cloud.get():
            _on_date_change(date_var, month_var)
    
    date_var.trace("w", _on_date_change_with_flag)

    # 按鈕框架
    btn_frame = create_btn_frame(frame, row=1, column=0)
    btn_frame.columnconfigure(0, weight=1)
    btn_frame.columnconfigure(1, weight=1)

    # 狀態顯示 Label
    status_var = tk.StringVar(value="")
    status_label = create_msg_label(frame, status_var, row=2, column=0, sticky="w")

    result: Dict[str, Any] = {"ok": False, "message": ""}

    create_btn_between(btn_frame, "保存", lambda: _handle_save(date_var, month_var, result, dlg, status_var, status_label), row=0, column=0, sticky="we")
    create_btn_last(btn_frame, "取消", lambda: close_dialog(dlg), row=0, column=1, sticky="we")

    dlg.protocol("WM_DELETE_WINDOW", lambda: close_dialog(dlg))
    
    # 背景執行緒抓取雲端預設值
    def _fetch_cloud_defaults() -> None:
        cloud_date, cloud_month = _load_current_config()
        # 在主執行緒中安全地更新值
        if dlg.winfo_exists():
            is_updating_from_cloud.set(True)
            dlg.after(0, lambda: date_var.set(cloud_date) if dlg.winfo_exists() else None)
            dlg.after(10, lambda: month_var.set(cloud_month) if dlg.winfo_exists() else None)
            dlg.after(20, lambda: is_updating_from_cloud.set(False) if dlg.winfo_exists() else None)
    
    thread = threading.Thread(target=_fetch_cloud_defaults, daemon=True)
    thread.start()
    
    dlg.wait_window(dlg)
    
    return result if result["ok"] else None


def _on_date_change(date_var: tk.StringVar, month_var: tk.StringVar) -> None:
    """日期變更時自動更新月份。"""
    date_str = date_var.get().strip()
    if date_str:
        month_var.set(_get_default_month(date_str))


def _handle_save(date_var: tk.StringVar, month_var: tk.StringVar, result: Dict[str, Any], dlg: tk.Toplevel, status_var: tk.StringVar, status_label: ttk.Label) -> None:
    """處理保存邏輯。"""
    date_str = date_var.get().strip()
    month_str = month_var.get().strip()

    # 驗證邏輯，錯誤顯示在 status_label 而非 messagebox
    if not date_str:
        status_var.set("請輸入執行日期")
        status_label.configure(foreground="red")
        return

    if not month_str:
        status_var.set("請輸入上傳月份")
        status_label.configure(foreground="red")
        return

    if not _validate_date(date_str):
        status_var.set("日期格式錯誤，請使用 YYYY-MM-DD")
        status_label.configure(foreground="red")
        return

    if not _validate_month(month_str):
        status_var.set("月份格式錯誤，請使用 YYYY/MM")
        status_label.configure(foreground="red")
        return

    if _update_scheduler_config(date_str, month_str):
        result["ok"] = True
        result["message"] = f"排程已更新: {date_str}"
        close_dialog(dlg)
    else:
        status_var.set("無法更新排程設定檔")
        status_label.configure(foreground="red")


def _load_current_config() -> tuple[str, str]:
    """從雲端 GitHub 讀取當前的 start_date 和 month。"""
    # 先嘗試從雲端讀取
    config = _fetch_scheduler_config_from_github()
    if config:
        for job in config.get("jobs", []):
            if job.get("name") == "union_raid_pipeline_once":
                start_date = job.get("start_date", _get_default_date())
                month = job.get("inputs", {}).get("month", _get_default_month(start_date))
                return start_date, month
    
    # 若雲端無法讀取，再試本地
    try:
        if SCHEDULER_CONFIG_PATH.exists():
            config = json.loads(SCHEDULER_CONFIG_PATH.read_text(encoding="utf-8"))
            for job in config.get("jobs", []):
                if job.get("name") == "union_raid_pipeline_once":
                    start_date = job.get("start_date", _get_default_date())
                    month = job.get("inputs", {}).get("month", _get_default_month(start_date))
                    return start_date, month
    except Exception:
        pass
    
    return _get_default_date(), _get_default_month(_get_default_date())


def _get_default_date() -> str:
    """取得預設日期（明天）。"""
    tomorrow = datetime.now() + timedelta(days=1)
    return tomorrow.strftime("%Y-%m-%d")


def _get_default_month(date_str: str) -> str:
    """根據日期推導月份。"""
    try:
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        return date_obj.strftime("%Y/%m")
    except ValueError:
        return ""


def _validate_date(date_str: str) -> bool:
    """驗證日期格式。"""
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _validate_month(month_str: str) -> bool:
    """驗證月份格式。"""
    try:
        parts = month_str.split("/")
        if len(parts) != 2:
            return False
        year = int(parts[0])
        month = int(parts[1])
        return 1 <= month <= 12 and 2000 <= year <= 2100
    except (ValueError, IndexError):
        return False


def _update_scheduler_config(date_str: str, month_str: str) -> bool:
    """透過 GitHub API 更新雲端的 scheduler_config.json。"""
    try:
        # 讀取本地配置
        if not SCHEDULER_CONFIG_PATH.exists():
            return False

        config = json.loads(SCHEDULER_CONFIG_PATH.read_text(encoding="utf-8"))

        # 修改配置
        for job in config.get("jobs", []):
            if job.get("name") == "union_raid_pipeline_once":
                job["start_date"] = date_str
                job["inputs"]["month"] = month_str
                break

        # 準備新內容
        new_content = json.dumps(config, ensure_ascii=False, indent=2)

        # 通過 GitHub API 推送
        if not _push_to_github(".github/runtime/scheduler_config.json", new_content):
            return False

        log_msg(f"Scheduler config updated on GitHub: date={date_str} month={month_str}")
        return True
    except Exception as exc:
        log_exc(exc)
        return False


def _get_github_token() -> str:
    """取得 GitHub token。"""
    for key in ("BLABLALINK_CLOUD_DATA_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        value = str(os.getenv(key, "")).strip()
        if value:
            return value

    gh_path = shutil.which("gh")
    if not gh_path:
        return ""
    try:
        result = subprocess.run(
            [gh_path, "auth", "token"],
            capture_output=True,
            text=True,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        return str(result.stdout or "").strip() if result.returncode == 0 else ""
    except Exception:
        return ""


def _fetch_scheduler_config_from_github() -> Optional[dict]:
    """從 GitHub 雲端讀取 scheduler_config.json。"""
    try:
        repo = _get_git_remote()
        if not repo:
            return None

        try:
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
            branch = result.stdout.strip() if result.returncode == 0 else "main"
        except Exception:
            branch = "main"

        url = f"https://api.github.com/repos/{repo}/contents/.github/runtime/scheduler_config.json?ref={branch}"
        response = _request_json(url)
        
        # GitHub API 返回的內容是 base64 編碼的
        if "content" in response:
            content_b64 = str(response["content"]).replace("\n", "")
            content = base64.b64decode(content_b64).decode("utf-8")
            return json.loads(content)
        return None
    except Exception as exc:
        log_exc(exc)
        return None


def _get_git_remote() -> Optional[str]:
    """從 git remote 取得儲存庫資訊。"""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        if result.returncode != 0:
            return None
        remote_url = result.stdout.strip()
        # 從 URL 提取 owner/repo
        if "github.com" in remote_url:
            if remote_url.startswith("git@"):
                # git@github.com:owner/repo.git
                parts = remote_url.split(":")[-1].replace(".git", "")
            else:
                # https://github.com/owner/repo.git
                parts = remote_url.split("/")[-2:]
                parts = f"{parts[0]}/{parts[1]}".replace(".git", "")
            return parts
        return None
    except Exception:
        return None


def _request_json(url: str, method: str = "GET", body: object | None = None, auth_required: bool = False) -> dict:
    """執行 GitHub API 請求。"""
    token = _get_github_token()
    if auth_required and not token:
        raise RuntimeError("GitHub 認證 token 不可用")

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "BlaBlaLinkTool",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")

    req = request.Request(url, data=data, headers=headers, method=method)
    with request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _push_to_github(rel_path: str, content: str) -> bool:
    """通過 GitHub API 推送檔案。"""
    try:
        repo = _get_git_remote()
        if not repo:
            log_msg("無法偵測到 GitHub 儲存庫")
            return False

        # 取得主分支名稱
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
            branch = result.stdout.strip() if result.returncode == 0 else "main"
        except Exception:
            branch = "main"

        url = f"https://api.github.com/repos/{repo}/contents/{rel_path}"
        sha = ""
        try:
            existing = _request_json(f"{url}?ref={branch}")
            sha = str(existing.get("sha", "") or "").strip()
        except error.HTTPError as exc:
            if exc.code != 404:
                raise

        payload = {
            "message": f"Update scheduler config via GUI",
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "branch": branch,
        }
        if sha:
            payload["sha"] = sha

        _request_json(url, method="PUT", body=payload, auth_required=True)
        return True
    except Exception as exc:
        log_exc(exc)
        return False

