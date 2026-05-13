# -*- coding: utf-8 -*-

from lib.jobs import healthcheck_upload


class FakeSpreadsheet:
    def __init__(self):
        self._worksheets = {}

    def worksheet(self, name):
        return self._worksheets[name]


class FakeWorksheet:
    def __init__(self, spreadsheet, title, values, *, col_count=5):
        self.title = title
        self._values = [list(row) for row in values]
        self.col_count = col_count
        self.row_count = max(len(values), 1)
        self.updates = []
        self.added_cols = 0
        self.deleted_rows = []
        spreadsheet._worksheets[title] = self

    def get_all_values(self):
        return [list(row) for row in self._values]

    def add_cols(self, count):
        self.added_cols += count
        self.col_count += count

    def update(self, cell_range, values):
        self.updates.append((cell_range, values))

    def delete_rows(self, start, end):
        self.deleted_rows.append((start, end))


def test_sync_healthcheck_sheet_uploads_functions_without_touching_extra_columns():
    spreadsheet = FakeSpreadsheet()
    ws = FakeWorksheet(
        spreadsheet,
        healthcheck_upload.HEALTHCHECK_WORKSHEET,
        [
            healthcheck_upload.HEALTHCHECK_HEADERS + ["備註"],
            ["Alice", "電擊", "輸出", "old1", "old2", "old3", "keep"],
            ["Removed", "水冷", "輔助", "R1", "R2", "R3", "old"],
        ],
        col_count=7,
    )

    synced = healthcheck_upload._sync_healthcheck_sheet(
        spreadsheet,
        [
            {
                "name": "Alice",
                "element": "燃燒",
                "type": "輸出",
                "function1": "優越代碼傷害",
                "function2": "攻擊力",
                "function3": "裝彈數",
            }
        ],
    )

    assert synced == [
        {
            "name": "Alice",
            "element": "燃燒",
            "type": "輸出",
            "function1": "優越代碼傷害",
            "function2": "攻擊力",
            "function3": "裝彈數",
        }
    ]
    assert ws.updates[0] == ("A1:F1", [healthcheck_upload.HEALTHCHECK_HEADERS])
    assert ws.updates[1] == (
        "A2:F2",
        [["Alice", "燃燒", "輸出", "優越代碼傷害", "攻擊力", "裝彈數"]],
    )
    assert ws.updates[2] == ("A3:F3", [["", "", "", "", "", ""]])
    assert ws.deleted_rows == []
