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


def test_build_updates_prefers_member_id_resolved_name_over_download_nickname():
    role_ws = FakeWorksheet(
        "role",
        [
            ["name", "", "", "Alice"],
            ["Old Alice", "", "", ""],
            ["Bob", "", "", ""],
        ],
    )
    member_ws = FakeWorksheet(
        "members",
        [
            ["name", "sync", "Blabla ID"],
            ["Old Alice", "", "id-a"],
            ["Bob", "", "id-b"],
        ],
    )

    nick_to_row_by_ws, member_id_to_nick, member_id_to_row_by_ws, header_pos = nikke_upload._load_sheet_headers(
        [role_ws],
        member_sheet=member_ws,
    )

    updates, _changes = nikke_upload._build_updates(
        [{"name": "Alice", "breakthrough": 6}],
        "New Alice",
        "id-a",
        [role_ws],
        nick_to_row_by_ws,
        member_id_to_nick,
        member_id_to_row_by_ws,
        header_pos,
        None,
        {},
        {},
    )

    assert list(updates) == [role_ws]
    assert updates[role_ws][0][0] == 2


def test_member_sheet_id_does_not_reuse_member_sheet_row_number():
    role_ws = FakeWorksheet(
        "role",
        [
            ["name", "", "", "Alice"],
            ["not used", "", "", ""],
            ["also not used", "", "", ""],
            ["Old Alice", "", "", ""],
        ],
    )
    member_ws = FakeWorksheet(
        "members",
        [
            ["name", "sync", "Blabla ID"],
            ["Old Alice", "", "id-a"],
        ],
    )

    nick_to_row_by_ws, member_id_to_nick, member_id_to_row_by_ws, header_pos = nikke_upload._load_sheet_headers(
        [role_ws],
        member_sheet=member_ws,
    )

    assert nick_to_row_by_ws[role_ws]["Old Alice"] == 4
    assert member_id_to_nick["id-a"] == "Old Alice"
    assert member_id_to_row_by_ws == {}
    assert header_pos[role_ws]["Alice"] == [4]

    updates, _changes = nikke_upload._build_updates(
        [{"name": "Alice", "breakthrough": 6}],
        "New Alice",
        "id-a",
        [role_ws],
        nick_to_row_by_ws,
        member_id_to_nick,
        member_id_to_row_by_ws,
        header_pos,
        None,
        {},
        {},
    )

    assert updates[role_ws][0][0] == 4


def test_each_role_sheet_uses_its_own_name_row():
    role_ws_a = FakeWorksheet(
        "role_a",
        [
            ["name", "", "", "Alice"],
            ["Old Alice", "", "", ""],
        ],
    )
    role_ws_b = FakeWorksheet(
        "role_b",
        [
            ["name", "", "", "Alice"],
            ["not used", "", "", ""],
            ["Old Alice", "", "", ""],
        ],
    )
    member_ws = FakeWorksheet(
        "members",
        [
            ["name", "sync", "Blabla ID"],
            ["Old Alice", "", "id-a"],
        ],
    )

    nick_to_row_by_ws, member_id_to_nick, member_id_to_row_by_ws, header_pos = nikke_upload._load_sheet_headers(
        [role_ws_a, role_ws_b],
        member_sheet=member_ws,
    )

    updates, _changes = nikke_upload._build_updates(
        [{"name": "Alice", "breakthrough": 6}],
        "New Alice",
        "id-a",
        [role_ws_a, role_ws_b],
        nick_to_row_by_ws,
        member_id_to_nick,
        member_id_to_row_by_ws,
        header_pos,
        None,
        {},
        {},
    )

    assert updates[role_ws_a][0][0] == 2
    assert updates[role_ws_b][0][0] == 3


def test_build_updates_falls_back_to_nickname_without_member_id():
    role_ws = FakeWorksheet(
        "role",
        [
            ["name", "", "Blabla ID", "Alice"],
            ["Old Alice", "", "id-a", ""],
            ["Bob", "", "", ""],
        ],
    )

    nick_to_row_by_ws, member_id_to_nick, member_id_to_row_by_ws, header_pos = nikke_upload._load_sheet_headers(
        [role_ws]
    )

    updates, _changes = nikke_upload._build_updates(
        [{"name": "Alice", "breakthrough": 6}],
        "Bob",
        "",
        [role_ws],
        nick_to_row_by_ws,
        member_id_to_nick,
        member_id_to_row_by_ws,
        header_pos,
        None,
        {},
        {},
    )

    assert list(updates) == [role_ws]
    assert updates[role_ws][0][0] == 3


def test_equivalent_role_names_match_across_sheet_and_download():
    worksheet = type("Worksheet", (), {"title": "角色練度_燃燒"})()
    worksheet.row_values = lambda row: ["成員", "拉毗：小紅帽"]
    worksheet.col_values = lambda col: ["", "", "Member"] if col == 1 else []
    nicknames, member_names, member_rows, headers = nikke_upload._load_sheet_headers([worksheet])
    updates, _ = nikke_upload._build_updates(
        [{"name": "拉毗: 小紅帽"}], "Member", "", [worksheet], nicknames,
        member_names, member_rows, headers, None, {}, {},
    )
    assert updates[worksheet][0][:2] == (3, 2)
