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


def test_sync_healthcheck_sheet_preserves_existing_extra_columns_and_fills_blank_nickname():
    spreadsheet = FakeSpreadsheet()
    ws = FakeWorksheet(
        spreadsheet,
        healthcheck_upload.HEALTHCHECK_WORKSHEET,
        [
            healthcheck_upload.HEALTHCHECK_HEADERS + ["備註"],
            ["Alice", "電擊", "輸出", "", "5", "keep"],
            ["Removed", "水冷", "輔助", "R", "", "old"],
        ],
        col_count=6,
    )

    synced = healthcheck_upload._sync_healthcheck_sheet(
        spreadsheet,
        [{"name": "Alice", "element": "燃燒", "type": "輸出"}],
    )

    assert synced == [
        {
            "name": "Alice",
            "element": "燃燒",
            "type": "輸出",
            "nickname": "Alice",
            "priority": "5",
        }
    ]
    assert ws.updates[0] == ("A1:E1", [healthcheck_upload.HEALTHCHECK_HEADERS])
    assert ws.updates[1][0] == "A2:F2"
    assert ws.updates[1][1] == [["Alice", "燃燒", "輸出", "Alice", "5", "keep"]]
    assert ws.deleted_rows == [(3, 3)]
