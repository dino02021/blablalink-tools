# -*- coding: utf-8 -*-

from types import SimpleNamespace

from lib.jobs import healthcheck_upload


def test_check_reads_common_source_without_uploading_another_copy(monkeypatch):
    snapshot = SimpleNamespace(spreadsheet_id="common", checklist_items=[(1001, "Alice")])
    spreadsheet = object()
    calls = []
    monkeypatch.setattr(healthcheck_upload, "load_healthcheck_snapshot", lambda: snapshot)
    monkeypatch.setattr(healthcheck_upload, "resolve_character_sheet_for_account", lambda account: ("3097", "union", "Union"))
    monkeypatch.setattr(healthcheck_upload, "get_gspread_client", lambda: SimpleNamespace(open_by_key=lambda key: spreadsheet))
    monkeypatch.setattr(healthcheck_upload, "validate_training_headers", lambda sheet, source: calls.append((sheet, source)))
    monkeypatch.setattr(healthcheck_upload, "ensure_healthcheck_snapshot_current", lambda source: calls.append(source))
    result = healthcheck_upload.run_healthcheck_upload("account")
    assert result.ok
    assert result.outputs["source_sheet_id"] == "common"
    assert calls == [(spreadsheet, snapshot), snapshot]


def test_check_reports_unsynchronized_headers(monkeypatch):
    monkeypatch.setattr(healthcheck_upload, "load_healthcheck_snapshot", lambda: object())
    monkeypatch.setattr(healthcheck_upload, "resolve_character_sheet_for_account", lambda account: ("3097", "union", "Union"))
    monkeypatch.setattr(healthcheck_upload, "get_gspread_client", lambda: SimpleNamespace(open_by_key=lambda key: object()))
    monkeypatch.setattr(healthcheck_upload, "validate_training_headers", lambda *args: (_ for _ in ()).throw(ValueError("請先在試算表執行健檢嚴格同步")))
    result = healthcheck_upload.run_healthcheck_upload("account")
    assert not result.ok
    assert "嚴格同步" in result.message
