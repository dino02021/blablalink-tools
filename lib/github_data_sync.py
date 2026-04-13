# -*- coding: utf-8 -*-
"""Cloud sync helpers for public data files stored in GitHub."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, Iterable, Optional
from urllib import error, request

from .constants import (
    FUNCTION_NAMES_FILE,
    NIKKE_CHECKLIST_NAME_CODES_FILE,
    NIKKES_INFO_FILE,
    NIKKES_NICKNAME_FILE,
    PROJECT_ROOT,
)
from .github_secrets_sync import DEFAULT_GITHUB_REPO
from .log import log_exc, log_msg

DATA_FILE_MAP: Dict[Path, str] = {
    NIKKES_INFO_FILE.resolve(): "data/nikkes_info.json",
    NIKKES_NICKNAME_FILE.resolve(): "data/nikkes_nickname.json",
    FUNCTION_NAMES_FILE.resolve(): "data/function_names.json",
    NIKKE_CHECKLIST_NAME_CODES_FILE.resolve(): "data/nikke_checklist_name_codes.json",
}

_watcher_lock = threading.Lock()
_watcher_started = False
_known_hashes: Dict[Path, Optional[str]] = {}


def _cloud_repo() -> str:
    value = str(os.getenv("BLABLALINK_CLOUD_DATA_REPO", "")).strip()
    if value:
        return value
    value = str(os.getenv("BLABLALINK_GITHUB_REPO", "")).strip()
    return value or DEFAULT_GITHUB_REPO


def _cloud_branch() -> str:
    return str(os.getenv("BLABLALINK_CLOUD_DATA_BRANCH", "main")).strip() or "main"


def _cloud_enabled() -> bool:
    raw = str(os.getenv("BLABLALINK_CLOUD_DATA_ENABLED", "1")).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _file_hash_from_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _read_text_if_exists(path: Path, encoding: str = "utf-8") -> Optional[str]:
    try:
        if not path.exists():
            return None
        return path.read_text(encoding=encoding)
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return None


def _github_token() -> str:
    for key in ("GH_TOKEN", "GITHUB_TOKEN"):
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
        )
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return ""

    if result.returncode != 0:
        return ""
    return str(result.stdout or "").strip()


def _request_json(url: str, method: str = "GET", body: object | None = None, auth_required: bool = False) -> dict:
    token = _github_token()
    if auth_required and not token:
        raise RuntimeError("GitHub auth token is unavailable")

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


def _fetch_remote_file(path: Path) -> Optional[str]:
    rel = DATA_FILE_MAP.get(path.resolve(), "")
    if not rel:
        return None

    repo = _cloud_repo()
    branch = _cloud_branch()
    url = f"https://api.github.com/repos/{repo}/contents/{rel}?ref={branch}"
    try:
        payload = _request_json(url)
    except error.HTTPError as exc:
        if exc.code == 404:
            return None
        log_exc(exc)
        return None
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return None

    content = str(payload.get("content", "") or "").replace("\n", "").strip()
    if not content:
        return None
    try:
        return base64.b64decode(content.encode("utf-8")).decode("utf-8")
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return None


def pull_cloud_data_files(paths: Iterable[Path] | None = None) -> None:
    if not _cloud_enabled():
        return

    targets = list(paths) if paths is not None else list(DATA_FILE_MAP.keys())
    for raw_path in targets:
        path = raw_path.resolve()
        rel = DATA_FILE_MAP.get(path, "")
        if not rel:
            continue
        remote_text = _fetch_remote_file(path)
        if remote_text is None:
            continue
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(remote_text, encoding="utf-8")
            _known_hashes[path] = _file_hash_from_text(remote_text)
            log_msg(f"github data pulled: repo={_cloud_repo()} branch={_cloud_branch()} path={rel}")
        except Exception as exc:  # noqa: BLE001
            log_exc(exc)


def _push_data_file(path: Path, content: str) -> None:
    rel = DATA_FILE_MAP.get(path.resolve(), "")
    if not rel or not _cloud_enabled():
        return

    repo = _cloud_repo()
    branch = _cloud_branch()
    url = f"https://api.github.com/repos/{repo}/contents/{rel}"
    sha = ""
    try:
        existing = _request_json(f"{url}?ref={branch}")
        sha = str(existing.get("sha", "") or "").strip()
    except error.HTTPError as exc:
        if exc.code != 404:
            raise

    payload = {
        "message": f"Update {rel}",
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha

    _request_json(url, method="PUT", body=payload, auth_required=True)
    log_msg(f"github data pushed: repo={repo} branch={branch} path={rel}")


def schedule_data_sync_for_path(path: Path, content: str) -> None:
    try:
        resolved = path.resolve()
    except Exception:
        return
    if resolved not in DATA_FILE_MAP:
        return
    try:
        _known_hashes[resolved] = _file_hash_from_text(content)
    except Exception:
        pass
    threading.Thread(target=_push_data_file, args=(resolved, content), daemon=True).start()


def write_data_text_and_sync_if_changed(path: Path, content: str, encoding: str = "utf-8") -> bool:
    try:
        existing = path.read_text(encoding=encoding) if path.exists() else None
    except Exception:
        existing = None

    if existing == content:
        return False

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding=encoding)
    schedule_data_sync_for_path(path, content)
    return True


def _poll_data_files(interval_seconds: float) -> None:
    while True:
        try:
            for raw_path in DATA_FILE_MAP.keys():
                path = raw_path.resolve()
                text = _read_text_if_exists(path)
                current_hash = _file_hash_from_text(text) if text is not None else None
                previous_hash = _known_hashes.get(path)
                if path not in _known_hashes:
                    _known_hashes[path] = current_hash
                    continue
                if current_hash == previous_hash:
                    continue
                _known_hashes[path] = current_hash
                if text is None:
                    continue
                schedule_data_sync_for_path(path, text)
        except Exception as exc:  # noqa: BLE001
            log_exc(exc)
        time.sleep(interval_seconds)


def start_cloud_data_watcher(interval_seconds: float = 2.0) -> None:
    global _watcher_started
    if not _cloud_enabled():
        return
    with _watcher_lock:
        if _watcher_started:
            return
        for raw_path in DATA_FILE_MAP.keys():
            path = raw_path.resolve()
            text = _read_text_if_exists(path)
            _known_hashes[path] = _file_hash_from_text(text) if text is not None else None
        threading.Thread(
            target=_poll_data_files,
            args=(interval_seconds,),
            daemon=True,
        ).start()
        _watcher_started = True
        log_msg(
            f"github data watcher started: repo={_cloud_repo()} branch={_cloud_branch()} base={PROJECT_ROOT}"
        )
