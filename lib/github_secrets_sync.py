# -*- coding: utf-8 -*-
"""GitHub secrets sync helpers for local runtime files."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, Optional

from .constants import (
    GOOGLE_CRED_FILE,
    GOOGLE_SHEETS_ID_FILE,
    SESSIONS_FILE,
    UNION_ACCOUNT_MAP_FILE,
)
from .log import log_exc, log_msg

DEFAULT_GITHUB_REPO = "dino02021/BlaBlaLinkTool"

SECRET_FILE_MAP: Dict[Path, str] = {
    SESSIONS_FILE.resolve(): "BLABLALINK_SESSIONS_JSON",
    GOOGLE_SHEETS_ID_FILE.resolve(): "BLABLALINK_SHEETS_JSON",
    GOOGLE_CRED_FILE.resolve(): "BLABLALINK_GOOGLE_CREDENTIALS_JSON",
    UNION_ACCOUNT_MAP_FILE.resolve(): "BLABLALINK_UNION_ACCOUNT_MAP_JSON",
}

_watcher_lock = threading.Lock()
_watcher_started = False
_known_hashes: Dict[Path, Optional[str]] = {}


def _resolve_secret_name(path: Path) -> str:
    try:
        return SECRET_FILE_MAP.get(path.resolve(), "")
    except Exception:
        return ""


def _github_repo() -> str:
    return str(os.getenv("BLABLALINK_GITHUB_REPO", DEFAULT_GITHUB_REPO)).strip() or DEFAULT_GITHUB_REPO


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


def _sync_secret_worker(secret_name: str, value: str, source_path: str) -> None:
    gh_path = shutil.which("gh")
    if not gh_path:
        log_msg(f"github secret sync skipped: gh not found secret={secret_name} source={source_path}")
        return

    try:
        result = subprocess.run(
            [gh_path, "secret", "set", secret_name, "--repo", _github_repo()],
            input=value,
            text=True,
            capture_output=True,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        detail = stderr or stdout or f"exit={result.returncode}"
        log_msg(f"github secret sync failed: secret={secret_name} source={source_path} detail={detail}")
        return

    log_msg(f"github secret synced: secret={secret_name} source={source_path}")


def schedule_secret_sync_for_path(path: Path, content: str) -> None:
    secret_name = _resolve_secret_name(path)
    if not secret_name:
        return
    try:
        _known_hashes[path.resolve()] = _file_hash_from_text(content)
    except Exception:
        pass
    threading.Thread(
        target=_sync_secret_worker,
        args=(secret_name, content, str(path)),
        daemon=True,
    ).start()


def write_text_and_sync_if_changed(path: Path, content: str, encoding: str = "utf-8") -> bool:
    try:
        existing = path.read_text(encoding=encoding) if path.exists() else None
    except Exception:
        existing = None

    if existing == content:
        log_msg(f"file unchanged, skip sync: {path}")
        return False

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding=encoding)
    schedule_secret_sync_for_path(path, content)
    return True


def _poll_secret_files(interval_seconds: float) -> None:
    while True:
        try:
            for raw_path in SECRET_FILE_MAP.keys():
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
                    log_msg(f"github secret watcher noticed removed file: {path}")
                    continue
                schedule_secret_sync_for_path(path, text)
        except Exception as exc:  # noqa: BLE001
            log_exc(exc)
        time.sleep(interval_seconds)


def start_secret_file_watcher(interval_seconds: float = 2.0) -> None:
    global _watcher_started
    with _watcher_lock:
        if _watcher_started:
            return
        for raw_path in SECRET_FILE_MAP.keys():
            path = raw_path.resolve()
            text = _read_text_if_exists(path)
            _known_hashes[path] = _file_hash_from_text(text) if text is not None else None
        threading.Thread(
            target=_poll_secret_files,
            args=(interval_seconds,),
            daemon=True,
        ).start()
        _watcher_started = True
        log_msg("github secret watcher started")
