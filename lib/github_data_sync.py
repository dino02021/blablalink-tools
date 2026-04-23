# -*- coding: utf-8 -*-
"""Direct GitHub-backed data access helpers for public data files."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Iterable, Optional
from urllib import error, request

from .constants import (
    FUNCTION_NAMES_FILE,
    NIKKE_HEALTHCHECK_MEMBERS_FILE,
    NIKKES_INFO_FILE,
    NIKKES_NICKNAME_FILE,
)
from .log import log_exc, log_msg

DEFAULT_CLOUD_DATA_REPO = "dino02021/BlaBlaLinkTool-data"

DATA_FILE_MAP: Dict[Path, str] = {
    NIKKES_INFO_FILE.resolve(): "data/nikkes_info.json",
    NIKKES_NICKNAME_FILE.resolve(): "data/nikkes_nickname.json",
    FUNCTION_NAMES_FILE.resolve(): "data/function_names.json",
    NIKKE_HEALTHCHECK_MEMBERS_FILE.resolve(): "data/nikke_healthcheck_members.json",
}


def _cloud_repo() -> str:
    value = str(os.getenv("BLABLALINK_CLOUD_DATA_REPO", "")).strip()
    return value or DEFAULT_CLOUD_DATA_REPO


def _cloud_branch() -> str:
    return str(os.getenv("BLABLALINK_CLOUD_DATA_BRANCH", "main")).strip() or "main"


def _cloud_enabled() -> bool:
    raw = str(os.getenv("BLABLALINK_CLOUD_DATA_ENABLED", "1")).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _subprocess_run_kwargs() -> dict:
    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return kwargs


def _github_token() -> str:
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
            **_subprocess_run_kwargs(),
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
    try:
        with request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        if exc.code not in {301, 302, 307, 308}:
            raise
        redirect_url = str(exc.headers.get("Location", "") or "").strip()
        if not redirect_url:
            raise
        redirected_req = request.Request(redirect_url, data=data, headers=headers, method=method)
        with request.urlopen(redirected_req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))


def _resolve_repo_path(path_or_rel: Path | str) -> str:
    if isinstance(path_or_rel, Path):
        resolved = path_or_rel.resolve()
        rel = DATA_FILE_MAP.get(resolved, "")
        if not rel:
            raise ValueError(f"Unsupported cloud data path: {path_or_rel}")
        return rel
    text = str(path_or_rel or "").strip()
    if not text:
        raise ValueError("Cloud data path is empty")
    return text


def _contents_url(rel_path: str) -> str:
    repo = _cloud_repo()
    branch = _cloud_branch()
    return f"https://api.github.com/repos/{repo}/contents/{rel_path}?ref={branch}"


def _fetch_remote_payload(rel_path: str) -> Optional[dict]:
    try:
        return _request_json(_contents_url(rel_path))
    except error.HTTPError as exc:
        if exc.code == 404:
            return None
        log_exc(exc)
        return None
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return None


def read_cloud_data_text(path_or_rel: Path | str, encoding: str = "utf-8") -> Optional[str]:
    if not _cloud_enabled():
        return None
    rel_path = _resolve_repo_path(path_or_rel)
    payload = _fetch_remote_payload(rel_path)
    if not payload:
        return None
    content = str(payload.get("content", "") or "").replace("\n", "").strip()
    if not content:
        return ""
    try:
        return base64.b64decode(content.encode("utf-8")).decode(encoding)
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return None


def read_cloud_data_json(path_or_rel: Path | str, default: object | None = None) -> object | None:
    text = read_cloud_data_text(path_or_rel, encoding="utf-8")
    if text is None:
        return default
    try:
        return json.loads(text)
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return default


def cloud_data_exists(path_or_rel: Path | str) -> bool:
    if not _cloud_enabled():
        return False
    rel_path = _resolve_repo_path(path_or_rel)
    return _fetch_remote_payload(rel_path) is not None


def write_cloud_data_text(path_or_rel: Path | str, content: str, encoding: str = "utf-8") -> bool:
    if not _cloud_enabled():
        raise RuntimeError("Cloud data sync is disabled")

    rel_path = _resolve_repo_path(path_or_rel)
    existing_text = read_cloud_data_text(rel_path, encoding=encoding)
    if existing_text == content:
        return False

    repo = _cloud_repo()
    branch = _cloud_branch()
    url = f"https://api.github.com/repos/{repo}/contents/{rel_path}"

    sha = ""
    payload = _fetch_remote_payload(rel_path)
    if payload:
        sha = str(payload.get("sha", "") or "").strip()

    body = {
        "message": f"Update {rel_path}",
        "content": base64.b64encode(content.encode(encoding)).decode("ascii"),
        "branch": branch,
    }
    if sha:
        body["sha"] = sha

    _request_json(url, method="PUT", body=body, auth_required=True)
    log_msg(f"github data pushed directly: repo={repo} branch={branch} path={rel_path}")
    return True


def write_data_text_and_sync_if_changed(path: Path | str, content: str, encoding: str = "utf-8") -> bool:
    return write_cloud_data_text(path, content, encoding=encoding)


def pull_cloud_data_files(paths: Iterable[Path] | None = None) -> None:
    _ = paths
    log_msg("pull_cloud_data_files skipped: data is read directly from GitHub")


def schedule_data_sync_for_path(path: Path, content: str) -> None:
    _ = (path, content)
    log_msg("schedule_data_sync_for_path skipped: data writes go directly to GitHub")


def flush_cloud_data_sync(timeout_seconds: float = 60.0) -> None:
    _ = timeout_seconds


def start_cloud_data_watcher(interval_seconds: float = 2.0) -> None:
    _ = interval_seconds
    log_msg("start_cloud_data_watcher skipped: data is read directly from GitHub")
