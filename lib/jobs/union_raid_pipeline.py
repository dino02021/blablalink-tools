# -*- coding: utf-8 -*-
"""Headless union raid pipeline job for single or multiple accounts."""

from __future__ import annotations

import base64
import json
import os
from typing import Any, Dict, List

from ..constants import UNION_WEB_APP_URL
from ..data_store import load_union_account_map
from ..log import log_exc
from .common import JobResult, resolve_account
from .union_raid_download import run_union_raid_download
from .union_raid_upload import resolve_latest_union_upload_month, run_union_raid_upload


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


def _is_union_upload_complete(result: JobResult) -> bool:
    if not result.ok:
        return False
    if result.stats.get("partial"):
        return False
    if result.stats.get("web_ok") is not True:
        return False
    return bool(str(result.outputs.get("sheet_id", "") or "").strip())


def run_raid_plan_web_app(sheet_id: str) -> JobResult:
    target_sheet_id = str(sheet_id or "").strip()
    if not target_sheet_id:
        return JobResult(ok=False, message="Missing spreadsheet id")
    if not UNION_WEB_APP_URL:
        return JobResult(ok=False, message="Missing UNION_WEB_APP_URL")

    try:
        import requests
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return JobResult(ok=False, message=f"requests import failed: {exc}")

    params = {
        "action": "raid_plan",
        "spreadsheet_id": target_sheet_id,
        "confirm": "1",
    }
    try:
        resp = requests.get(UNION_WEB_APP_URL, params=params, timeout=600)
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return JobResult(ok=False, message=str(exc), outputs={"sheet_id": target_sheet_id})

    body_text = (resp.text or "").strip()
    outputs = {
        "sheet_id": target_sheet_id,
        "web_app_response": body_text[:500],
    }
    if 200 <= resp.status_code < 300:
        lower_body = body_text.lower()
        if lower_body in {"unauthorized", "bad_request", "busy"} or lower_body.startswith("error:"):
            return JobResult(ok=False, message=body_text[:300] or "Raid plan WebApp failed", outputs=outputs)
        return JobResult(ok=True, message=body_text[:300] or "raid plan ok", outputs=outputs)

    return JobResult(
        ok=False,
        message=f"HTTP {resp.status_code} {body_text[:300]}",
        outputs=outputs,
    )


def run_union_raid_pipeline(
    mode: str = "download",
    scope: str = "single",
    account: str | None = None,
    month: str | None = None,
    season_id: str | None = None,
) -> JobResult:
    mode_text = str(mode or "download").strip().lower() or "download"
    if mode_text not in {"download", "full"}:
        return JobResult(ok=False, message=f"Unsupported mode: {mode}")

    scope_text = str(scope or "single").strip().lower() or "single"
    if scope_text not in {"single", "all"}:
        return JobResult(ok=False, message=f"Unsupported scope: {scope}")

    accounts = _resolve_accounts(scope_text, account)
    if not accounts:
        if scope_text == "all":
            return JobResult(ok=False, message="No accounts found in BLABLALINK_UNION_ACCOUNT_MAP_JSON")
        return JobResult(ok=False, message="Missing account")

    requested_month = str(month or "").strip()
    requested_season_id = str(season_id or "").strip()
    results: List[Dict[str, Any]] = []
    ok_count = 0

    for slug in accounts:
        entry: Dict[str, Any] = {
            "account": slug,
            "mode": mode_text,
            "season_id": requested_season_id,
            "ok": False,
        }

        download_result = run_union_raid_download(account=slug, season_id=requested_season_id or None)
        entry["download"] = _serialize_result(download_result)
        entry["message"] = download_result.message

        if not download_result.ok:
            results.append(entry)
            continue

        if mode_text == "download":
            entry["ok"] = True
            ok_count += 1
            results.append(entry)
            continue

        data_path = str(download_result.outputs.get("data_path", "")).strip() or None
        month_value = requested_month
        if not month_value:
            try:
                month_value = resolve_latest_union_upload_month(data_path=data_path)
            except Exception as exc:  # noqa: BLE001
                entry["message"] = str(exc)
                entry["resolved_month"] = ""
                results.append(entry)
                continue

        upload_result = run_union_raid_upload(
            account=slug,
            month=month_value,
            data_path=data_path,
        )
        entry["resolved_month"] = month_value
        entry["upload"] = _serialize_result(upload_result)
        entry["message"] = upload_result.message
        entry["ok"] = upload_result.ok

        if upload_result.ok and _is_union_upload_complete(upload_result):
            plan_result = run_raid_plan_web_app(str(upload_result.outputs.get("sheet_id", "") or ""))
            entry["raid_plan"] = _serialize_result(plan_result)
            if plan_result.ok:
                entry["message"] = f"{upload_result.message}; 排刀完成: {plan_result.message}"
                entry["ok"] = True
            else:
                entry["message"] = f"{upload_result.message}; 排刀失敗: {plan_result.message}"
                entry["ok"] = False
        elif upload_result.ok:
            entry["message"] = f"{upload_result.message}; 未執行排刀: 上傳或 WebApp 同步尚未完整成功"
            entry["ok"] = False

        if entry["ok"]:
            ok_count += 1

        results.append(entry)

    failed_count = len(results) - ok_count
    summary = {
        "mode": mode_text,
        "scope": scope_text,
        "season_id": requested_season_id,
        "total": len(results),
        "ok_count": ok_count,
        "failed_count": failed_count,
    }
    if failed_count:
        message = f"union raid pipeline finished with failures: ok={ok_count} failed={failed_count}"
    else:
        message = f"union raid pipeline finished: ok={ok_count}"

    return JobResult(
        ok=failed_count == 0,
        message=message,
        stats=summary,
        outputs={"results": results},
    )
