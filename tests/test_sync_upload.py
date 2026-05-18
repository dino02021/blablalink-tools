# -*- coding: utf-8 -*-

from lib.jobs import sync_upload


class FakeWorksheet:
    def __init__(self, values):
        self._values = [list(row) for row in values]
        self.batch_updates = []

    def col_values(self, col):
        out = []
        for row in self._values:
            value = row[col - 1] if col - 1 < len(row) else ""
            out.append(value)
        while out and out[-1] == "":
            out.pop()
        return out

    def batch_update(self, updates, value_input_option=None):
        self.batch_updates.append((updates, value_input_option))


def test_sync_member_levels_prefers_member_id_falls_back_to_name_and_appends():
    ws = FakeWorksheet(
        [
            ["名稱", "同步", "member_id", "D", "E"],
            ["Old Alice", "1", "id-a", "keep-d2", "keep-e2"],
            ["Bob", "2", "", "keep-d3", "keep-e3"],
        ]
    )

    stats = sync_upload._sync_member_levels_to_worksheet(
        ws,
        [
            {"nickname": "Alice Renamed", "member_id": "id-a", "synchro_level": "301"},
            {"nickname": "Bob", "member_id": "id-b", "synchro_level": 302},
            {"nickname": "Carol", "member_id": "id-c", "synchro_level": 303},
        ],
    )

    assert stats == {
        "matched_count": 2,
        "appended_count": 1,
        "updated_count": 3,
        "total_count": 3,
    }
    assert ws.batch_updates == [
        (
            [
                {"range": "B2:B3", "values": [[""], [""]]},
            ],
            "RAW",
        ),
        (
            [
                {"range": "B2:C2", "values": [[301, "id-a"]]},
                {"range": "B3:C3", "values": [[302, "id-b"]]},
                {"range": "A4:C4", "values": [["Carol", 303, "id-c"]]},
            ],
            "RAW",
        )
    ]
    touched_ranges = [item["range"] for updates, _option in ws.batch_updates for item in updates]
    assert all(":D" not in rng and ":E" not in rng for rng in touched_ranges)


def test_sync_member_levels_without_member_id_updates_only_level_on_name_match():
    ws = FakeWorksheet(
        [
            ["名稱", "同步", "member_id"],
            ["Alice", "1", "existing-id"],
        ]
    )

    stats = sync_upload._sync_member_levels_to_worksheet(
        ws,
        [{"nickname": "Alice", "member_id": "", "synchro_level": 400}],
    )

    assert stats["matched_count"] == 1
    assert ws.batch_updates[0][0] == [{"range": "B2:B2", "values": [[""]]}]
    assert ws.batch_updates[1][0] == [
        {"range": "B2:B2", "values": [[400]]},
    ]
