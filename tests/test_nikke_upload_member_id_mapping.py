# -*- coding: utf-8 -*-

from lib.gui.dialogs import nikke_upload


class FakeWorksheet:
    def __init__(self, title, rows):
        self.title = title
        self._rows = rows

    def col_values(self, col):
        values = []
        for row in self._rows:
            values.append(row[col - 1] if col - 1 < len(row) else "")
        while values and values[-1] == "":
            values.pop()
        return values

    def row_values(self, row):
        if row - 1 >= len(self._rows):
            return []
        return list(self._rows[row - 1])


def test_build_updates_prefers_member_id_over_nickname():
    ws = FakeWorksheet(
        "角色練度",
        [
            ["名稱", "", "Blabla ID", "Alice"],
            ["Old Alice", "", "id-a", ""],
            ["Bob", "", "id-b", ""],
        ],
    )

    nick_to_row, member_id_to_row, header_pos = nikke_upload._load_sheet_headers([ws])

    updates, _changes = nikke_upload._build_updates(
        [{"name": "Alice", "breakthrough": 6}],
        "New Alice",
        "id-a",
        [ws],
        nick_to_row,
        member_id_to_row,
        header_pos,
        None,
        {},
        {},
    )

    assert list(updates) == [ws]
    assert updates[ws][0][0] == 2


def test_load_sheet_headers_reads_member_id_from_member_sheet():
    role_ws = FakeWorksheet(
        "角色練度",
        [
            ["名稱", "", "", "Alice"],
            ["not used", "", "", ""],
        ],
    )
    member_ws = FakeWorksheet(
        "成員名稱",
        [
            ["名稱", "同步", "Blabla ID"],
            ["Old Alice", "", "id-a"],
        ],
    )

    nick_to_row, member_id_to_row, header_pos = nikke_upload._load_sheet_headers([role_ws], member_sheet=member_ws)

    assert nick_to_row["Old Alice"] == 2
    assert member_id_to_row["id-a"] == 2
    assert header_pos[role_ws]["Alice"] == [4]


def test_build_updates_falls_back_to_nickname_without_member_id():
    ws = FakeWorksheet(
        "角色練度",
        [
            ["名稱", "", "Blabla ID", "Alice"],
            ["Old Alice", "", "id-a", ""],
            ["Bob", "", "", ""],
        ],
    )

    nick_to_row, member_id_to_row, header_pos = nikke_upload._load_sheet_headers([ws])

    updates, _changes = nikke_upload._build_updates(
        [{"name": "Alice", "breakthrough": 6}],
        "Bob",
        "",
        [ws],
        nick_to_row,
        member_id_to_row,
        header_pos,
        None,
        {},
        {},
    )

    assert list(updates) == [ws]
    assert updates[ws][0][0] == 3
