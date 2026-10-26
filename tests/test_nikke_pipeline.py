# -*- coding: utf-8 -*-

from lib.jobs import nikke_pipeline
from lib.jobs.common import JobResult
from types import SimpleNamespace


def test_all_scope_continues_but_fails_when_account_download_fails(monkeypatch):
    calls = []

    monkeypatch.setattr(
        nikke_pipeline,
        "_resolve_accounts",
        lambda scope, account: ["bad@example.com", "good@example.com"],
    )

    snapshot = SimpleNamespace(function_id_targets={"1001": [70005, None, None]})
    reads = []
    monkeypatch.setattr(nikke_pipeline, "load_healthcheck_snapshot", lambda: reads.append(True) or snapshot)

    def fake_download(account=None, healthcheck_snapshot=None):
        assert healthcheck_snapshot is snapshot
        calls.append(("download", account, healthcheck_snapshot.function_id_targets))
        if account == "bad@example.com":
            return JobResult(ok=False, message="找不到聯盟資訊")
        return JobResult(
            ok=True,
            message="download ok",
            outputs={"guild_id": "3097", "files": ["member.json"]},
        )

    def fake_upload(account=None, files=None, sheet_id=None, healthcheck_snapshot=None, expected_members=None):
        assert healthcheck_snapshot is snapshot
        calls.append(("upload", account, list(files or []), sheet_id))
        return JobResult(
            ok=False,
            message="upload partial",
            stats={"ok_count": 31, "total_count": 32, "failed_count": 1},
            outputs={"failed_files": [{"file": "member.json", "nickname": "member", "message": "找不到成員列或角色欄位"}]},
        )

    monkeypatch.setattr(nikke_pipeline, "run_nikke_batch_download", fake_download)
    monkeypatch.setattr(nikke_pipeline, "run_nikke_upload", fake_upload)
    monkeypatch.setattr(
        nikke_pipeline,
        "resolve_character_sheet_target",
        lambda guild_id: ("sheet-id", "月下"),
    )

    result = nikke_pipeline.run_nikke_pipeline(scope="all")

    assert result.ok is False
    assert result.stats == {
        "scope": "all",
        "total": 2,
        "ok_count": 0,
        "skipped_count": 0,
        "failed_count": 2,
        "upload_ok_count": 31,
        "upload_total_count": 32,
    }
    assert result.message == "\n".join(
        [
            "上傳妮姬資料完成 (31/32)",
            "月下 (3097): 31/32",
            "失敗:",
            "- - / bad@example.com: 找不到聯盟資訊",
            "- 月下 (3097) / good@example.com / member.json: 找不到成員列或角色欄位",
        ]
    )
    assert result.outputs["unions"] == [
        {"account": "good@example.com", "union_id": "3097", "sheet_name": "月下", "ok_count": 31, "total_count": 32,
         "download_failed_count": 0, "skipped_count": 0}
    ]
    assert result.outputs["failures"] == [
        {
            "account": "bad@example.com",
            "stage": "download",
            "message": "找不到聯盟資訊",
        },
        {
            "account": "good@example.com",
            "stage": "upload",
            "union_id": "3097",
            "sheet_name": "月下",
            "file": "member.json",
            "nickname": "member",
            "message": "找不到成員列或角色欄位",
        },
    ]
    assert result.outputs["skipped"] == []
    assert result.outputs["_summary_only"] is True
    assert calls == [
        ("download", "bad@example.com", {"1001": [70005, None, None]}),
        ("download", "good@example.com", {"1001": [70005, None, None]}),
        ("upload", "good@example.com", ["member.json"], "sheet-id"),
    ]
    assert len(reads) == 1


def test_unreadable_source_stops_before_any_download(monkeypatch):
    monkeypatch.setattr(nikke_pipeline, "_resolve_accounts", lambda *args: ["account"])
    monkeypatch.setattr(nikke_pipeline, "load_healthcheck_snapshot", lambda: (_ for _ in ()).throw(ValueError("sheet unavailable")))
    monkeypatch.setattr(nikke_pipeline, "run_nikke_batch_download", lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not download")))
    result = nikke_pipeline.run_nikke_pipeline()
    assert not result.ok
    assert "sheet unavailable" in result.message


def test_unmapped_union_is_skipped_without_upload(monkeypatch):
    monkeypatch.setattr(nikke_pipeline, "_resolve_accounts", lambda *args: ["account"])
    monkeypatch.setattr(nikke_pipeline, "load_healthcheck_snapshot", lambda: object())
    monkeypatch.setattr(nikke_pipeline, "run_nikke_batch_download", lambda **kwargs: JobResult(
        ok=False, message="missing target", outputs={"guild_id": "3097", "skip_reason": "missing target"},
    ))
    monkeypatch.setattr(nikke_pipeline, "run_nikke_upload", lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not upload")))
    result = nikke_pipeline.run_nikke_pipeline(scope="all")
    assert result.ok
    assert result.stats["skipped_count"] == 1
    assert result.outputs["skipped"] == [{"account": "account", "union_id": "3097", "message": "missing target"}]


def test_partial_downloads_upload_successful_members_and_keep_original_totals(monkeypatch):
    calls = []
    manifest = [{"nickname": "good", "member_id": "id1", "expected_name_codes": ["1001"]}]
    failed = [{"nickname": "missing", "member_id": "id2", "message": "download failed"}]
    monkeypatch.setattr(nikke_pipeline, "_resolve_accounts", lambda *args: ["partial", "complete"])
    monkeypatch.setattr(nikke_pipeline, "load_healthcheck_snapshot", lambda: object())
    monkeypatch.setattr(nikke_pipeline, "resolve_character_sheet_target", lambda _: ("sheet", "月下"))

    def download(account=None, **kwargs):
        calls.append(("download", account))
        is_complete = account == "complete"
        return JobResult(ok=is_complete, message="download ok" if is_complete else "下載失敗：missing",
            stats={"ok_count": 1, "total_count": 1 if is_complete else 3,
                   "failed_count": 0 if is_complete else 1, "skipped_count": 0 if is_complete else 1},
            outputs={"guild_id": "3097", "files": ["good.json"], "expected_members": manifest,
                     "is_upload_ready": True, "failed_members": [] if is_complete else failed})

    def upload(account=None, files=None, expected_members=None, **kwargs):
        calls.append(("upload", account))
        assert files == ["good.json"]
        assert expected_members is manifest
        return JobResult(ok=True, message="uploaded", stats={"ok_count": 1, "total_count": 1})

    monkeypatch.setattr(nikke_pipeline, "run_nikke_batch_download", download)
    monkeypatch.setattr(nikke_pipeline, "run_nikke_upload", upload)
    result = nikke_pipeline.run_nikke_pipeline(scope="all")
    assert calls == [("download", "partial"), ("upload", "partial"), ("download", "complete"), ("upload", "complete")]
    assert not result.ok
    assert result.stats["ok_count"] == 1
    assert result.stats["upload_ok_count"] == 2
    assert result.stats["upload_total_count"] == 4
    assert result.outputs["unions"][0]["total_count"] == 3
    assert result.outputs["unions"][0]["download_failed_count"] == 1
    assert result.outputs["failures"][0]["failed_members"] == failed
    assert "missing" in result.message
    assert "正常略過 1" in result.message
