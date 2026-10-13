# -*- coding: utf-8 -*-

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from lib.google_sheets import auth
from lib.gui.dialogs import nikke_upload as helpers
from lib.healthcheck_config import HealthcheckSnapshot, build_download_context
from lib.jobs import nikke_upload, nikke_batch_download


@pytest.fixture
def upload_environment(monkeypatch, tmp_path):
    member = {"name_code": 1001, "name": "Alice", "element": "燃燒", "type": "輸出"}
    snapshot = HealthcheckSnapshot("common", "version", [member], [(1001, "Alice")], {"1001": [70005, 70008, 70007]})
    worksheet = type("Worksheet", (), {"title": "角色練度_燃燒"})()
    worksheet.row_values = lambda row: ["成員", "Alice"]
    worksheet.col_values = lambda col: ["", "", "Member"] if col == 1 else []
    spreadsheet = SimpleNamespace(worksheets=lambda: [worksheet])
    monkeypatch.setattr(nikke_upload, "resolve_character_sheet_for_account", lambda *args, **kwargs: ("3097", "union", "Union"))
    monkeypatch.setattr(auth, "get_gspread_client", lambda: SimpleNamespace(open_by_key=lambda key: spreadsheet))
    monkeypatch.setattr(nikke_upload, "ensure_healthcheck_snapshot_current", lambda snapshot: None)
    monkeypatch.setattr(nikke_upload, "validate_training_headers", lambda sheet, source: "header")
    writes = []
    monkeypatch.setattr(helpers, "_apply_updates", lambda updates: writes.append(updates) or (True, []))
    payload = {
        "nickname": "Member", "member_id": "",
        "healthcheck_context": build_download_context(snapshot, "union", "header"),
        "character_details": [{"name": "Alice", "name_code": 1001, "functions": [
            {"function_id": 70005, "function_value": 10},
            {"function_id": 70008, "function_value": 20},
            {"function_id": 70007, "function_value": 30},
        ]}],
    }
    return tmp_path, snapshot, payload, writes


def test_valid_file_uploads_the_verified_trait_values(upload_environment):
    directory, snapshot, payload, writes = upload_environment
    (directory / "member.json").write_text(json.dumps(payload), encoding="utf-8")
    result = nikke_upload.run_nikke_upload(account="account", input_dir=directory, healthcheck_snapshot=snapshot)
    assert result.ok
    assert len(writes) == 1
    assert next(iter(writes[0].values()))[0][2][-3:] == [10, 20, 30]


@pytest.mark.parametrize("failure", ["old_file", "late_invalid_file", "source_changed_before_write", "headers_changed_before_write"])
def test_failed_preflight_writes_no_member_data(upload_environment, monkeypatch, failure):
    directory, snapshot, payload, writes = upload_environment
    if failure == "old_file":
        del payload["healthcheck_context"]
    if failure == "late_invalid_file":
        invalid = deepcopy(payload)
        invalid["healthcheck_context"]["source_fingerprint"] = "old"
        (directory / "z-invalid.json").write_text(json.dumps(invalid), encoding="utf-8")
    if failure == "source_changed_before_write":
        calls = []
        def check_source(source):
            calls.append(source)
            if len(calls) == 2:
                raise ValueError("名單已變更")
        monkeypatch.setattr(nikke_upload, "ensure_healthcheck_snapshot_current", check_source)
    if failure == "headers_changed_before_write":
        headers = iter(["header", "changed-header"])
        monkeypatch.setattr(nikke_upload, "validate_training_headers", lambda *args: next(headers))
    (directory / "a-member.json").write_text(json.dumps(payload), encoding="utf-8")
    result = nikke_upload.run_nikke_upload(account="account", input_dir=directory, healthcheck_snapshot=snapshot)
    assert not result.ok
    assert ("重新下載" if failure in ("old_file", "late_invalid_file") else "已變更") in result.message
    assert writes == []


@pytest.mark.asyncio
async def test_unsynchronized_target_stops_before_fetching_member_data(monkeypatch):
    async def union_info(ctx):
        return "3097", "member", "area"
    async def fetch_members(*args, **kwargs):
        pytest.fail("must not start member download")
    monkeypatch.setattr(nikke_batch_download, "fetch_union_info", union_info)
    monkeypatch.setattr(nikke_batch_download, "fetch_guild_members", fetch_members)
    monkeypatch.setattr(nikke_batch_download, "resolve_character_sheet_target", lambda guild: ("union", "Union"))
    monkeypatch.setattr(nikke_batch_download, "get_gspread_client", lambda: SimpleNamespace(open_by_key=lambda key: object()))
    monkeypatch.setattr(nikke_batch_download, "ensure_healthcheck_snapshot_current", lambda source: None)
    monkeypatch.setattr(nikke_batch_download, "validate_training_headers", lambda *args: (_ for _ in ()).throw(ValueError("嚴格同步")))
    with pytest.raises(ValueError, match="嚴格同步"):
        await nikke_batch_download._batch_download_with_ctx(object(), [], {}, object())
