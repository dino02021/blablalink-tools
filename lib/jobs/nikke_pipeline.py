# -*- coding: utf-8 -*-
"""Headless Nikke pipeline job."""

from __future__ import annotations

from typing import Any, Dict

from .common import JobResult, resolve_account
from .nikke_batch_download import run_nikke_batch_download
from .nikke_upload import run_nikke_upload


def _serialize_result(result: JobResult) -> Dict[str, Any]:
    return {
        "ok": result.ok,
        "message": result.message,
        "stats": result.stats,
        "outputs": result.outputs,
    }


def run_nikke_pipeline(account: str | None = None) -> JobResult:
    slug = resolve_account(account)
    if not slug:
        return JobResult(ok=False, message="Missing account")

    download_result = run_nikke_batch_download(account=slug)
    outputs: Dict[str, Any] = {
        "account": slug,
        "download": _serialize_result(download_result),
    }
    if not download_result.ok:
        return JobResult(
            ok=False,
            message=download_result.message,
            stats={
                "account": slug,
                "stage": "download",
                **download_result.stats,
            },
            outputs=outputs,
        )

    files = [
        str(item).strip()
        for item in (download_result.outputs.get("files", []) or [])
        if str(item).strip()
    ]
    upload_result = run_nikke_upload(account=slug, files=files)
    outputs["upload"] = _serialize_result(upload_result)

    if not upload_result.ok:
        return JobResult(
            ok=False,
            message=upload_result.message,
            stats={
                "account": slug,
                "stage": "upload",
                **upload_result.stats,
            },
            outputs=outputs,
        )

    return JobResult(
        ok=True,
        message=upload_result.message,
        stats={
            "account": slug,
            "stage": "done",
            **upload_result.stats,
        },
        outputs=outputs,
    )
