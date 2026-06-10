# -*- coding: utf-8 -*-

from lib.jobs import sync_upload
from lib.jobs.common import JobResult


def test_run_sync_upload_all_continues_after_exception(monkeypatch):
    monkeypatch.setattr(sync_upload, "_resolve_accounts", lambda scope, account: ["boom", "ok"])

    def fake_single(account=None, worksheet_name="members"):
        if account == "boom":
            raise RuntimeError("network failed")
        return JobResult(
            ok=True,
            message="ok",
            stats={"updated_count": 2, "total_count": 3},
            outputs={"union_id": "3097", "sheet_name": "union-a"},
        )

    monkeypatch.setattr(sync_upload, "_run_sync_upload_single", fake_single)

    result = sync_upload.run_sync_upload(scope="all")

    assert result.ok is False
    assert result.stats["ok_count"] == 1
    assert result.stats["failed_count"] == 1
    assert result.outputs["failures"] == [{"account": "boom", "message": "network failed"}]
    assert result.outputs["unions"][0]["account"] == "ok"


def test_run_sync_upload_all_treats_ok_without_stats_as_failure(monkeypatch):
    monkeypatch.setattr(sync_upload, "_resolve_accounts", lambda scope, account: ["bad", "ok"])

    def fake_single(account=None, worksheet_name="members"):
        if account == "bad":
            return JobResult(ok=True, message="sheet skipped")
        return JobResult(
            ok=True,
            message="ok",
            stats={"updated_count": 2, "total_count": 3},
            outputs={"union_id": "3097", "sheet_name": "union-a"},
        )

    monkeypatch.setattr(sync_upload, "_run_sync_upload_single", fake_single)

    result = sync_upload.run_sync_upload(scope="all")

    assert result.ok is False
    assert result.stats["ok_count"] == 1
    assert result.stats["failed_count"] == 1
    assert result.outputs["failures"][0]["account"] == "bad"
    assert result.outputs["failures"][0]["message"] == "sheet skipped"
