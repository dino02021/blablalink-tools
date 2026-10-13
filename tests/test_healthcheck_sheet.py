# -*- coding: utf-8 -*-

from copy import deepcopy

import pytest

from lib.google_sheets import healthcheck


class Worksheet:
    def __init__(self, rows):
        self.rows = deepcopy(rows)
        self.writes = []

    def get(self, cell_range):
        assert cell_range == "A1:F"
        return [row[:6] for row in self.rows]

    def batch_update(self, updates, value_input_option):
        assert value_input_option == "RAW"
        for update in updates:
            row_index = int(update["range"].split(":")[0][1:]) - 1
            self.rows[row_index][:6] = update["values"][0]
        self.writes.append(updates)

    def delete_rows(self, index):
        del self.rows[index - 1]
        self.writes.append(("delete", index))

    def append_rows(self, rows, value_input_option, insert_data_option, table_range):
        assert value_input_option == "RAW"
        assert table_range == "A:F"
        assert insert_data_option == "INSERT_ROWS"
        self.rows.extend(deepcopy(rows))
        self.writes.append(("append", rows))


def test_edits_and_deletion_preserve_extra_cells_with_the_correct_role(monkeypatch):
    worksheet = Worksheet([
        healthcheck.HEALTHCHECK_HEADERS + ["評分追蹤", "暱稱", "優先度"],
        ["Alice", "燃燒", "輸出", "攻擊力", "", "", "追蹤", "A", "高"],
        ["Removed", "水冷", "輔助", "", "", "", "", "R", "低"],
        ["Bob", "鐵甲", "輔助", "", "", "", "追蹤", "B", "中"],
    ])
    monkeypatch.setattr(healthcheck, "_open_healthcheck_worksheet", lambda: worksheet)
    expected = healthcheck.read_healthcheck_rows()
    healthcheck.write_healthcheck_rows([
        {"name": "Bob", "element": "鐵甲", "type": "輔助"},
        {"name": "Alice", "element": "燃燒", "type": "輔助", "function1": "最大裝彈數"},
        {"name": "New", "element": "電擊", "type": "輸出"},
    ], expected_rows=expected)
    assert worksheet.rows[1] == ["Alice", "燃燒", "輔助", "最大裝彈數", "", "", "追蹤", "A", "高"]
    assert worksheet.rows[2] == ["Bob", "鐵甲", "輔助", "", "", "", "追蹤", "B", "中"]
    assert worksheet.rows[3] == ["New", "電擊", "輸出", "", "", ""]


def test_stale_edit_cannot_overwrite_new_sheet_settings(monkeypatch):
    worksheet = Worksheet([healthcheck.HEALTHCHECK_HEADERS, ["Alice", "燃燒", "輸出", "攻擊力"]])
    monkeypatch.setattr(healthcheck, "_open_healthcheck_worksheet", lambda: worksheet)
    expected = healthcheck.read_healthcheck_rows()
    worksheet.rows[1][3] = "最大裝彈數"
    with pytest.raises(ValueError, match="已變更"):
        healthcheck.write_healthcheck_rows(expected, expected_rows=expected)
    assert worksheet.writes == []


def test_fingerprint_accepts_equivalent_trait_formatting():
    original = [{"name": "Alice", "function1": "【 攻擊力增加 】"}]
    normalized = [{"name": "Alice", "function1": "攻擊力"}]
    assert healthcheck.healthcheck_rows_fingerprint(original) == healthcheck.healthcheck_rows_fingerprint(normalized)


def test_source_requires_the_expected_six_column_contract(monkeypatch):
    monkeypatch.setattr(healthcheck, "_open_healthcheck_worksheet", lambda: Worksheet([["角色", "詞條"]]))
    with pytest.raises(ValueError, match="A-F"):
        healthcheck.read_healthcheck_rows()
