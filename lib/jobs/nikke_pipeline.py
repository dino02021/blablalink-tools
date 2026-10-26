# -*- coding: utf-8 -*-
"""Headless Nikke pipeline job for single or multiple accounts."""

from __future__ import annotations

import base64
import json
import os
from typing import Any, Dict, List

from ..data_store import load_union_account_map
from ..healthcheck_config import load_healthcheck_snapshot
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


def _failure_entry(account: str, stage: str, result: JobResult | None = None, message: str | None = None) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "account": account,
        "stage": stage,
        "message": str(message if message is not None else (result.message if result else "")).strip(),
    }
    if result and result.stats:
        entry["stats"] = result.stats
    return entry


def _union_label(item: Dict[str, Any]) -> str:
    sheet_name = str(item.get("sheet_name", "") or "").strip()
    union_id = str(item.get("union_id", "") or item.get("guild_id", "") or "").strip()
    if sheet_name and union_id:
        return f"{sheet_name} ({union_id})"
    return sheet_name or union_id or "-"


def _build_pipeline_message(
    upload_ok_count: int,
    upload_total_count: int,
    union_summaries: List[Dict[str, Any]],
    failures: List[Dict[str, Any]],
    download_warnings: List[Dict[str, Any]],
    skipped: List[Dict[str, Any]],
) -> str:
    status = "完成" if failures else "成功"
    member_skipped_count = sum(int(item.get("skipped_count", 0) or 0) for item in union_summaries)
    skipped_text = f"，正常略過 {member_skipped_count}" if member_skipped_count else ""
    lines = [f"上傳妮姬資料{status} ({upload_ok_count}/{upload_total_count}{skipped_text})"]
    for item in union_summaries:
        details = []
        if item.get("download_failed_count"):
            details.append(f"下載失敗 {item['download_failed_count']}")
        if item.get("skipped_count"):
            details.append(f"正常略過 {item['skipped_count']}")
        suffix = "，" + "，".join(details) if details else ""
        lines.append(f"{_union_label(item)}: {item.get('ok_count', 0)}/{item.get('total_count', 0)}{suffix}")
    if download_warnings:
        lines.append("下載警告:")
        for item in download_warnings:
            failed_members = item.get("failed_members", [])
            names = "、".join(
                str(member.get("nickname", "")).strip()
                for member in failed_members
                if isinstance(member, dict) and str(member.get("nickname", "")).strip()
            )
            member_part = f"：{names}" if names else ""
            lines.append(f"- {_union_label(item)} / {item.get('account', '-')}: {item.get('message', '')}{member_part}")
    if failures:
        lines.append("失敗:")
        for item in failures:
            file_text = str(item.get("file", "") or "").strip()
            file_part = f" / {file_text}" if file_text else ""
            lines.append(
                f"- {_union_label(item)} / {item.get('account', '-')}{file_part}: {item.get('message', '')}"
            )
    if skipped:
        lines.append("跳過:")
        for item in skipped:
            lines.append(f"- {_union_label(item)} / {item.get('account', '-')}: {item.get('message', '')}")
    return "\n".join(lines)


def run_nikke_pipeline(scope: str = "single", account: str | None = None) -> JobResult:
    scope_text = str(scope or "single").strip().lower() or "single"
    if scope_text not in {"single", "all"}:
        return JobResult(ok=False, message=f"Unsupported scope: {scope}")

    accounts = _resolve_accounts(scope_text, account)
    if not accounts:
        if scope_text == "all":
            return JobResult(ok=False, message="No accounts found in BLABLALINK_UNION_ACCOUNT_MAP_JSON")
        return JobResult(ok=False, message="Missing account")

    failures: List[Dict[str, Any]] = []
    download_warnings: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    union_summaries: List[Dict[str, Any]] = []
    ok_count = 0
    skipped_count = 0
    upload_ok_count = 0
    upload_total_count = 0
    try:
        snapshot = load_healthcheck_snapshot()
    except Exception as exc:
        return JobResult(ok=False, message=f"讀取共用健檢名單失敗: {exc}")

    for slug in accounts:
        download_result = run_nikke_batch_download(account=slug, healthcheck_snapshot=snapshot)

        if not download_result.ok and not download_result.outputs.get("is_upload_ready"):
            if download_result.outputs.get("skip_reason"):
                skipped_count += 1
                skipped.append({"account": slug, "union_id": download_result.outputs.get("guild_id", ""), "message": download_result.message})
                continue
            failed_members = download_result.outputs.get("failed_members", [])
            if failed_members:
                download_warnings.append({
                    "account": slug,
                    "union_id": download_result.outputs.get("guild_id", ""),
                    "message": download_result.message,
                    "failed_members": failed_members,
                })
                continue
            failures.append(_failure_entry(slug, "download", download_result))
            continue

        guild_id = str(download_result.outputs.get("guild_id", "") or "").strip()
        files = [
            str(item).strip()
            for item in (download_result.outputs.get("files", []) or [])
            if str(item).strip()
        ]

        try:
            target_sheet_id, target_sheet_name = resolve_character_sheet_target(guild_id)
        except ValueError as exc:
            skipped_count += 1
            skipped.append({"account": slug, "union_id": guild_id, "message": str(exc)})
            continue

        if not download_result.ok:
            download_warnings.append({
                "account": slug,
                "union_id": guild_id,
                "sheet_name": target_sheet_name,
                "message": download_result.message,
                "failed_members": download_result.outputs.get("failed_members", []),
            })

        upload_result = run_nikke_upload(account=slug, files=files, sheet_id=target_sheet_id, healthcheck_snapshot=snapshot,
                                         expected_members=download_result.outputs.get("expected_members"))
        account_ok_count = int(upload_result.stats.get("ok_count", 0) or 0)
        account_total_count = max(int(upload_result.stats.get("total_count", 0) or 0),
                                  int(download_result.stats.get("total_count", 0) or 0))
        upload_ok_count += account_ok_count
        upload_total_count += account_total_count
        union_summaries.append(
            {
                "account": slug,
                "union_id": guild_id,
                "sheet_name": target_sheet_name,
                "ok_count": account_ok_count,
                "total_count": account_total_count,
                "download_failed_count": int(download_result.stats.get("failed_count", 0) or 0),
                "skipped_count": int(download_result.stats.get("skipped_count", 0) or 0),
            }
        )

        if upload_result.ok:
            ok_count += 1
            continue
        upload_failures = upload_result.outputs.get("failed_files", []) if upload_result.outputs else []
        if isinstance(upload_failures, list) and upload_failures:
            for item in upload_failures:
                if not isinstance(item, dict):
                    continue
                failures.append(
                    {
                        "account": slug,
                        "stage": "upload",
                        "union_id": guild_id,
                        "sheet_name": target_sheet_name,
                        "file": item.get("file", ""),
                        "nickname": item.get("nickname", ""),
                        "message": item.get("message", upload_result.message),
                    }
                )
        else:
            entry = _failure_entry(slug, "upload", upload_result)
            entry["union_id"] = guild_id
            entry["sheet_name"] = target_sheet_name
            failures.append(entry)

    failed_count = len(failures)
    summary = {
        "scope": scope_text,
        "total": len(accounts),
        "ok_count": ok_count,
        "skipped_count": skipped_count,
        "failed_count": failed_count,
        "upload_ok_count": upload_ok_count,
        "upload_total_count": upload_total_count,
        "download_warning_count": len(download_warnings),
        "download_failed_member_count": sum(len(item.get("failed_members", [])) for item in download_warnings),
    }
    message = _build_pipeline_message(upload_ok_count, upload_total_count, union_summaries, failures, download_warnings, skipped)

    return JobResult(
        ok=failed_count == 0,
        message=message,
        stats=summary,
        outputs={"_summary_only": True, "unions": union_summaries, "failures": failures,
                 "download_warnings": download_warnings, "skipped": skipped},
    )
