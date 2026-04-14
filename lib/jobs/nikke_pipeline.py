# -*- coding: utf-8 -*-
"""Headless Nikke pipeline job for single or multiple accounts."""

from __future__ import annotations

import base64
import json
import os
from typing import Any, Dict, List

from ..data_store import load_union_account_map
from .common import JobResult, resolve_account
from .nikke_batch_download import run_nikke_batch_download
from .nikke_upload import resolve_character_sheet_target, run_nikke_upload


def _clean_union_account_map(raw: Any) -> List[Dict[str, str]]:
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


def _parse_union_account_map_text(raw_text: str) -> List[Dict[str, str]]:
    try:
        raw = json.loads(raw_text)
    except Exception:
        return []
    return _clean_union_account_map(raw)


def _parse_union_account_map_base64(raw_text: str) -> List[Dict[str, str]]:
    try:
        decoded = base64.b64decode(raw_text.encode("utf-8"), validate=True).decode("utf-8")
    except Exception:
        return []
    return _parse_union_account_map_text(decoded)


def _load_union_account_map_from_env() -> List[Dict[str, str]]:
    raw_text = str(os.getenv("BLABLALINK_UNION_ACCOUNT_MAP_JSON", "")).strip()
    if not raw_text:
        return load_union_account_map()

    items = _parse_union_account_map_text(raw_text)
    if items:
        return items

    items = _parse_union_account_map_base64(raw_text)
    if items:
        return items

    return load_union_account_map()


def _resolve_accounts(scope: str, account: str | None) -> List[str]:
    scope_text = str(scope or "single").strip().lower() or "single"
    if scope_text == "single":
        slug = resolve_account(account)
        return [slug] if slug else []

    if scope_text != "all":
        return []

    items = _load_union_account_map_from_env()
    accounts: List[str] = []
    seen: set[str] = set()
    for item in items:
        username = str(item.get("username", "")).strip()
        if not username or username in seen:
            continue
        seen.add(username)
        accounts.append(username)
    return accounts


def _serialize_result(result: JobResult) -> Dict[str, Any]:
    return {
        "ok": result.ok,
        "message": result.message,
        "stats": result.stats,
        "outputs": result.outputs,
    }


def run_nikke_pipeline(scope: str = "single", account: str | None = None) -> JobResult:
    scope_text = str(scope or "single").strip().lower() or "single"
    if scope_text not in {"single", "all"}:
        return JobResult(ok=False, message=f"Unsupported scope: {scope}")

    accounts = _resolve_accounts(scope_text, account)
    if not accounts:
        if scope_text == "all":
            return JobResult(ok=False, message="No accounts found in BLABLALINK_UNION_ACCOUNT_MAP_JSON")
        return JobResult(ok=False, message="Missing account")

    results: List[Dict[str, Any]] = []
    ok_count = 0
    skipped_count = 0

    for slug in accounts:
        entry: Dict[str, Any] = {
            "account": slug,
            "ok": False,
            "skipped": False,
        }

        download_result = run_nikke_batch_download(account=slug)
        entry["download"] = _serialize_result(download_result)
        entry["message"] = download_result.message

        if not download_result.ok:
            entry["stage"] = "download"
            results.append(entry)
            continue

        guild_id = str(download_result.outputs.get("guild_id", "") or "").strip()
        files = [
            str(item).strip()
            for item in (download_result.outputs.get("files", []) or [])
            if str(item).strip()
        ]
        entry["guild_id"] = guild_id
        entry["files"] = files

        try:
            target_sheet_id, target_sheet_name = resolve_character_sheet_target(guild_id)
        except ValueError as exc:
            entry["ok"] = True
            entry["skipped"] = True
            entry["stage"] = "skipped"
            entry["message"] = str(exc)
            skipped_count += 1
            results.append(entry)
            continue

        entry["sheet_id"] = target_sheet_id
        entry["sheet_name"] = target_sheet_name
        upload_result = run_nikke_upload(account=slug, files=files, sheet_id=target_sheet_id)
        entry["upload"] = _serialize_result(upload_result)
        entry["message"] = upload_result.message
        entry["ok"] = upload_result.ok
        entry["stage"] = "done" if upload_result.ok else "upload"

        if upload_result.ok:
            ok_count += 1

        results.append(entry)

    failed_count = sum(1 for item in results if not bool(item.get("ok")))
    summary = {
        "scope": scope_text,
        "total": len(results),
        "ok_count": ok_count,
        "skipped_count": skipped_count,
        "failed_count": failed_count,
    }
    if failed_count:
        message = (
            "nikke pipeline finished with failures: "
            f"ok={ok_count} skipped={skipped_count} failed={failed_count}"
        )
    else:
        message = f"nikke pipeline finished: ok={ok_count} skipped={skipped_count}"

    return JobResult(
        ok=failed_count == 0,
        message=message,
        stats=summary,
        outputs={"results": results},
    )
