# -*- coding: utf-8 -*-

from lib.jobs import nikke_pipeline
from lib.jobs.common import JobResult


def test_all_scope_continues_but_fails_when_account_download_fails(monkeypatch):
    calls = []

    monkeypatch.setattr(
        nikke_pipeline,
        "_resolve_accounts",
        lambda scope, account: ["bad@example.com", "good@example.com"],
    )

    monkeypatch.setattr(nikke_pipeline, "build_healthcheck_function_id_targets", lambda: {"1001": [70005, None, None]})

    def fake_download(account=None, function_id_targets=None):
        calls.append(("download", account, function_id_targets))
        if account == "bad@example.com":
            return JobResult(ok=False, message="找不到聯盟資訊")
        return JobResult(
            ok=True,
            message="download ok",
            outputs={"guild_id": "3097", "files": ["member.json"]},
        )

    def fake_upload(account=None, files=None, sheet_id=None):
        calls.append(("upload", account, list(files or []), sheet_id))
        return JobResult(ok=True, message="upload ok", stats={"ok_count": 31, "total_count": 32})

    monkeypatch.setattr(nikke_pipeline, "run_nikke_batch_download", fake_download)
    monkeypatch.setattr(nikke_pipeline, "run_nikke_upload", fake_upload)
    monkeypatch.setattr(
        nikke_pipeline,
        "resolve_character_sheet_target",
        lambda guild_id: ("sheet-id", "sheet-name"),
    )

    result = nikke_pipeline.run_nikke_pipeline(scope="all")

    assert result.ok is False
    assert result.stats == {
        "scope": "all",
        "total": 2,
        "ok_count": 1,
        "skipped_count": 0,
        "failed_count": 1,
        "upload_ok_count": 31,
        "upload_total_count": 32,
    }
    assert result.message == "上傳妮姬資料完成 (31/32)，失敗 1 筆"
    assert result.outputs == {
        "failures": [
            {
                "account": "bad@example.com",
                "stage": "download",
                "message": "找不到聯盟資訊",
            }
        ],
        "skipped": [],
    }
    assert calls == [
        ("download", "bad@example.com", {"1001": [70005, None, None]}),
        ("download", "good@example.com", {"1001": [70005, None, None]}),
        ("upload", "good@example.com", ["member.json"], "sheet-id"),
    ]
