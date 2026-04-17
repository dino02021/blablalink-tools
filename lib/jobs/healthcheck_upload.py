# -*- coding: utf-8 -*-
"""Upload healthcheck member list to the CharacterDetails spreadsheet."""

from __future__ import annotations

import json
from typing import Any, Dict
from urllib import error, request

from ..constants import WEB_APP_URL
from ..data_store import build_healthcheck_upload_rows
from ..log import log_exc
from .common import JobResult
from .nikke_upload import resolve_character_sheet_for_account


def _post_healthcheck_sync(sheet_id: str, rows: list[Dict[str, str]]) -> tuple[bool, str]:
    if not WEB_APP_URL:
        return False, "WEB_APP_URL 未設定"

    payload = {
        "kind": "sync_healthcheck_members",
        "sheet_id": sheet_id,
        "rows": rows,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        WEB_APP_URL,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=600) as resp:
            body = resp.read().decode("utf-8", errors="replace").strip()
            ok = body.lower() == "ok"
            return ok, body or ("ok" if ok else "unknown_response")
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return False, body or f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return False, str(exc)


def run_healthcheck_upload(account: str | None = None) -> JobResult:
    rows = build_healthcheck_upload_rows()
    if not rows:
        return JobResult(ok=False, message="nikke_healthcheck_members.json 沒有可上傳的資料")

    try:
        union_id, target_sheet_id, target_sheet_name = resolve_character_sheet_for_account(account)
    except ValueError as exc:
        return JobResult(ok=False, message=str(exc))

    ok, message = _post_healthcheck_sync(target_sheet_id, rows)
    outputs = {
        "union_id": union_id,
        "sheet_id": target_sheet_id,
        "sheet_name": target_sheet_name,
        "row_count": len(rows),
    }
    if not ok:
        return JobResult(ok=False, message=message or "健檢名單同步失敗", outputs=outputs)
    return JobResult(
        ok=True,
        message=f"健檢名單已同步 ({len(rows)})",
        outputs=outputs,
    )
