# -*- coding: utf-8 -*-

from copy import deepcopy
from types import SimpleNamespace

import pytest

from lib import data_store, healthcheck_config as config
from lib.google_sheets.healthcheck import healthcheck_rows_fingerprint


def source_rows():
    return [{"name": "Alice", "element": "燃燒", "type": "輸出", "function1": "優越代碼傷害", "function2": "攻擊力", "function3": "最大裝彈數"}]


@pytest.fixture
def snapshot(monkeypatch):
    monkeypatch.setattr(config, "read_healthcheck_rows", source_rows)
    monkeypatch.setattr(data_store, "_build_nikke_info_lookup", lambda: {1001: {"name": "Alice", "element": "Fire"}})
    monkeypatch.setattr(data_store, "load_healthcheck_function_definitions", lambda: [
        {"name": "優越代碼傷害", "function_id": 70005}, {"name": "攻擊力", "function_id": 70008}, {"name": "最大裝彈數", "function_id": 70007},
    ])
    return config.load_healthcheck_snapshot()


class Spreadsheet:
    def __init__(self):
        self.sheet = SimpleNamespace(title="角色練度_燃燒", col_count=13)
        self.values = [
            ["成員", "Alice"] + [""] * 11,
            ["", "突破", "技能", "", "", "裝備 (頭/手/身/腳)", "", "", "", "收藏品", "詞條 (優/攻/彈)", "", ""],
        ]

    def worksheets(self):
        return [self.sheet]

    def values_batch_get(self, ranges):
        assert ranges == ["'角色練度_燃燒'!1:2"]
        return {"valueRanges": [{"values": deepcopy(self.values)}]}


def document(snapshot, fingerprint):
    return {
        "healthcheck_context": config.build_download_context(snapshot, "union", fingerprint),
        "character_details": [{"name": "Alice", "name_code": 1001, "functions": [
            {"function_id": 70005}, {"function_id": 70008}, {"function_id": 70007},
        ]}],
    }


def test_download_selection_and_trait_order_share_one_source_read(snapshot):
    assert snapshot.checklist_items == [(1001, "Alice")]
    assert snapshot.function_id_targets == {"1001": [70005, 70008, 70007]}
    assert snapshot.fingerprint == healthcheck_rows_fingerprint(source_rows())


def test_valid_headers_and_download_context(snapshot):
    fingerprint = config.validate_training_headers(Spreadsheet(), snapshot)
    config.validate_download_context(document(snapshot, fingerprint), snapshot, "union", fingerprint)


@pytest.mark.parametrize("position,value", [(1, "Bob"), (2, "Bob")])
def test_missing_or_extra_roles_require_strict_sync(snapshot, position, value):
    spreadsheet = Spreadsheet()
    spreadsheet.values[0][position] = value
    with pytest.raises(ValueError, match="嚴格同步"):
        config.validate_training_headers(spreadsheet, snapshot)


@pytest.mark.parametrize("offset,label", [(1, "技能"), (2, "突破"), (5, "收藏品"), (10, "詞條 (攻/優/彈)")])
def test_wrong_block_positions_or_trait_order_are_rejected(snapshot, offset, label):
    spreadsheet = Spreadsheet()
    spreadsheet.values[1][offset] = label
    with pytest.raises(ValueError, match="嚴格同步"):
        config.validate_training_headers(spreadsheet, snapshot)


def test_changed_source_stops_upload(snapshot, monkeypatch):
    rows = source_rows()
    rows[0]["function1"] = "蓄力速度"
    monkeypatch.setattr(config, "read_healthcheck_rows", lambda: rows)
    with pytest.raises(ValueError, match="已變更"):
        config.ensure_healthcheck_snapshot_current(snapshot)


def test_empty_attribute_sheet_is_valid_when_no_roles_are_selected(snapshot):
    spreadsheet = Spreadsheet()
    empty = SimpleNamespace(title="角色練度_水冷", col_count=13)
    spreadsheet.worksheets = lambda: [spreadsheet.sheet, empty]
    spreadsheet.values_batch_get = lambda ranges: {"valueRanges": [{"values": spreadsheet.values}, {"values": [["成員"]]}]}
    assert config.validate_training_headers(spreadsheet, snapshot)


def test_dual_element_role_must_exist_in_both_attribute_sheets(snapshot):
    member = {**snapshot.members[0], "name": "拉毗：小紅帽"}
    dual_snapshot = config.HealthcheckSnapshot(snapshot.spreadsheet_id, snapshot.fingerprint, [member], [(1001, member["name"])], snapshot.function_id_targets)
    spreadsheet = Spreadsheet()
    spreadsheet.values[0][1] = member["name"]
    with pytest.raises(ValueError, match="鐵甲"):
        config.validate_training_headers(spreadsheet, dual_snapshot)


@pytest.mark.parametrize("mutation", ["old_file", "target", "trait_order", "missing_role_id", "header"])
def test_stale_or_misrouted_files_are_rejected(snapshot, mutation):
    payload = document(snapshot, "header")
    if mutation == "old_file":
        del payload["healthcheck_context"]
    elif mutation == "target":
        payload["healthcheck_context"]["target_spreadsheet_id"] = "different-union"
    elif mutation == "trait_order":
        payload["character_details"][0]["functions"].reverse()
    elif mutation == "missing_role_id":
        del payload["character_details"][0]["name_code"]
    else:
        payload["healthcheck_context"]["target_header_fingerprint"] = "old-header"
    with pytest.raises(ValueError, match="重新下載"):
        config.validate_download_context(payload, snapshot, "union", "header")
