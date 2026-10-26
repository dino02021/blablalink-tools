# -*- coding: utf-8 -*-

from copy import deepcopy
from itertools import count

import pytest

from lib.jobs import sync_upload
from lib.jobs.common import JobResult


SHEET_IDS = count()


class FakeSpreadsheet:
    def __init__(self, sheets):
        self.sheets = sheets
        self.batch_requests = []
        self.request_states = []
        self.rejected_sheet_id = None
        for sheet in sheets:
            sheet.spreadsheet = self

    def worksheets(self):
        return self.sheets

    def worksheet(self, title):
        return next(sheet for sheet in self.sheets if sheet.title == title)

    def batch_update(self, body):
        self.batch_requests.append(deepcopy(body))
        staged = {sheet.id: deepcopy(sheet._values) for sheet in self.sheets}
        metadata = {sheet.id: deepcopy(sheet.row_metadata) for sheet in self.sheets}
        for request in body["requests"]:
            kind, operation = next(iter(request.items()))
            target = operation.get("source", operation.get("range", operation.get("start", operation)))
            sheet_id = target["sheetId"]
            if sheet_id == self.rejected_sheet_id:
                raise RuntimeError("training sheet rejected update")
            rows = staged[sheet_id]
            tags = metadata[sheet_id]
            sheet = next(sheet for sheet in self.sheets if sheet.id == sheet_id)
            if kind == "appendDimension":
                assert operation["dimension"] == "ROWS"
                rows.extend([[""] * sheet.col_count for _ in range(operation["length"])])
                tags.extend({} for _ in range(operation["length"]))
            elif kind == "insertDimension":
                start, end = target["startIndex"], target["endIndex"]
                assert 0 <= start < end <= len(rows)
                rows[start:start] = [[""] * sheet.col_count for _ in range(end - start)]
                tags[start:start] = [{} for _ in range(end - start)]
            elif kind == "moveDimension":
                start, end = target["startIndex"], target["endIndex"]
                destination = operation["destinationIndex"]
                assert 0 <= start < end <= len(rows)
                moved_rows, moved_tags = rows[start:end], tags[start:end]
                del rows[start:end]
                del tags[start:end]
                if destination >= end:
                    destination -= end - start
                rows[destination:destination] = moved_rows
                tags[destination:destination] = moved_tags
            elif kind == "repeatCell":
                assert operation["fields"] == "userEnteredValue,note"
                assert target["endRowIndex"] <= len(rows)
                for index in range(target["startRowIndex"], target["endRowIndex"]):
                    rows[index] = [""] * sheet.col_count
                    tags[index].pop("note", None)
            elif kind == "updateCells":
                assert operation["fields"] == "userEnteredValue"
                for offset, row_data in enumerate(operation["rows"]):
                    row_index = target["rowIndex"] + offset
                    assert row_index < len(rows)
                    for column, cell in enumerate(row_data["values"], start=target["columnIndex"]):
                        assert column < sheet.col_count
                        value = next(iter(cell["userEnteredValue"].values()))
                        rows[row_index][column] = value
            else:
                raise AssertionError(f"unexpected request {kind}")
            self.request_states.append((deepcopy(request), deepcopy(staged)))
        for sheet in self.sheets:
            sheet.added_rows += len(staged[sheet.id]) - sheet.row_count
            sheet._values = staged[sheet.id]
            sheet.row_metadata = metadata[sheet.id]
            sheet.row_count = len(sheet._values)


class FakeWorksheet:
    def __init__(self, values, title=""):
        self.id = next(SHEET_IDS)
        self.col_count = max(3, max((len(row) for row in values), default=1))
        self._values = [list(row) + [""] * (self.col_count - len(row)) for row in values]
        self.row_count = len(self._values)
        self.row_metadata = [{} for _ in self._values]
        self.batch_updates = []
        self.added_rows = 0
        self.title = title
        FakeSpreadsheet([self])

    def col_values(self, col):
        values = [row[col - 1] for row in self._values]
        while values and values[-1] == "":
            values.pop()
        return values

    def get_all_values(self):
        return deepcopy(self._values)


def test_sync_member_levels_prefers_member_id_falls_back_to_name_and_appends():
    ws = FakeWorksheet(
        [
            ["名稱", "同步", "member_id", "D", "E"],
            ["Old Alice", "1", "id-a", "keep-d2", "keep-e2"],
            ["Bob", "2", "", "keep-d3", "keep-e3"],
            ["", "", "", "", ""],
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

    assert stats == {"matched_count": 2, "appended_count": 1, "updated_count": 3, "total_count": 3}
    assert ws._values[1:] == [
        ["Alice Renamed", 301, "id-a", "keep-d2", "keep-e2"],
        ["Bob", 302, "id-b", "keep-d3", "keep-e3"],
        ["Carol", 303, "id-c", "", ""],
    ]
    assert len(ws.spreadsheet.batch_requests) == 1


def test_sync_member_levels_removes_unmatched_rows_and_appends_at_bottom():
    ws = FakeWorksheet(
        [
            ["名稱", "同步", "member_id", "D", "E"],
            ["Alice", "1", "id-a", "alice-d", "alice-e"],
            ["Old Member", "2", "id-old", "old-d", "old-e"],
            ["Bob", "3", "id-b", "bob-d", "bob-e"],
        ]
    )
    stats = sync_upload._sync_member_levels_to_worksheet(
        ws,
        [
            {"nickname": "Alice Renamed", "member_id": "id-a", "synchro_level": 301},
            {"nickname": "Bob", "member_id": "id-b", "synchro_level": 303},
            {"nickname": "Carol", "member_id": "id-c", "synchro_level": 304},
        ],
    )
    assert stats["matched_count"] == 2
    assert stats["appended_count"] == 1
    assert ws.added_rows == 0
    assert ws._values[1:] == [
        ["Alice Renamed", 301, "id-a", "alice-d", "alice-e"],
        ["Bob", 303, "id-b", "bob-d", "bob-e"],
        ["Carol", 304, "id-c", "", ""],
    ]


def test_sync_member_levels_rejects_upload_when_existing_rows_are_insufficient():
    ws = FakeWorksheet([["名稱", "同步", "member_id"], ["Alice", "1", "id-a"]])
    before = deepcopy(ws._values)
    with pytest.raises(ValueError, match="現有資料列不足"):
        sync_upload._sync_member_levels_to_worksheet(
            ws,
            [
                {"nickname": "New Alice", "member_id": "id-new-a", "synchro_level": 301},
                {"nickname": "New Bob", "member_id": "id-new-b", "synchro_level": 302},
            ],
        )
    assert ws.added_rows == 0
    assert ws._values == before
    assert ws.spreadsheet.batch_requests == []


def test_sync_member_levels_shifts_character_sheets_with_their_own_start_rows():
    member_ws = FakeWorksheet(
        [
            ["名稱", "同步", "member_id", "D", "E"],
            ["Alice", "1", "id-a", "alice-d", "alice-e"],
            ["Old Member", "2", "id-old", "old-d", "old-e"],
            ["Bob", "3", "id-b", "bob-d", "bob-e"],
        ],
        title="成員名稱",
    )
    attribute_ws = FakeWorksheet(
        [
            ["成員", "灰姑娘", "欄位3", "欄位4"],
            ["", "突破", "技能", "裝備"],
            ["Alice", "alice-1", "alice-2", "alice-3"],
            ["Old Member", "old-1", "old-2", "old-3"],
            ["Bob", "bob-1", "bob-2", "bob-3"],
        ],
        title="角色練度_電擊",
    )
    change_ws = FakeWorksheet(
        [
            ["成員", "電擊", "水冷", "燃燒"],
            ["", "灰姑娘", "白雪公主", "拉毗"],
            ["", "突破", "技能", "變化"],
            ["Alice", "alice-c1", "alice-c2", "alice-c3"],
            ["Old Member", "old-c1", "old-c2", "old-c3"],
            ["Bob", "bob-c1", "bob-c2", "bob-c3"],
        ],
        title="角色練度_變化",
    )
    workbook = FakeSpreadsheet([member_ws, attribute_ws, change_ws])
    attribute_headers = deepcopy(attribute_ws._values[:2])
    change_headers = deepcopy(change_ws._values[:3])
    sync_upload._sync_member_levels_to_worksheet(
        member_ws,
        [
            {"nickname": "Alice Renamed", "member_id": "id-a", "synchro_level": 301},
            {"nickname": "Bob", "member_id": "id-b", "synchro_level": 303},
            {"nickname": "Carol", "member_id": "id-c", "synchro_level": 304},
        ],
        character_sheets=[attribute_ws, change_ws],
    )
    assert attribute_ws._values[:2] == attribute_headers
    assert change_ws._values[:3] == change_headers
    assert attribute_ws._values[2:] == [
        ["Alice Renamed", "alice-1", "alice-2", "alice-3"],
        ["Bob", "bob-1", "bob-2", "bob-3"],
        ["Carol", "", "", ""],
    ]
    assert change_ws._values[3:] == [
        ["Alice Renamed", "alice-c1", "alice-c2", "alice-c3"],
        ["Bob", "bob-c1", "bob-c2", "bob-c3"],
        ["Carol", "", "", ""],
    ]
    assert len(workbook.batch_requests) == 1


def test_sync_member_levels_without_member_id_updates_only_level_on_name_match():
    ws = FakeWorksheet([["名稱", "同步", "member_id"], ["Alice", "1", "existing-id"]])
    stats = sync_upload._sync_member_levels_to_worksheet(
        ws, [{"nickname": "Alice", "member_id": "", "synchro_level": 400}]
    )
    assert stats["matched_count"] == 1
    assert ws._values[1] == ["Alice", 400, "existing-id"]


def test_sync_member_levels_reads_all_sheets_before_changing_members():
    member_ws = FakeWorksheet(
        [["名稱", "同步", "member_id"], ["Old", 300, "old-id"]],
        title="成員名稱",
    )

    class UnavailableWorksheet(FakeWorksheet):
        def get_all_values(self):
            raise RuntimeError("cannot read training sheet")

    training_ws = UnavailableWorksheet([["角色"], ["欄位"], ["Old"]], title="角色練度_電擊")
    with pytest.raises(RuntimeError, match="cannot read training sheet"):
        sync_upload._sync_member_levels_to_worksheet(
            member_ws,
            [{"nickname": "New", "member_id": "new-id", "synchro_level": 400}],
            character_sheets=[training_ws],
        )

    assert member_ws.batch_updates == []
    assert member_ws.added_rows == 0


@pytest.mark.parametrize("nickname,level", [("Bob", None), ("Bob", "unavailable"), (None, 401)])
def test_sync_member_levels_does_not_remove_member_with_unavailable_level(nickname, level):
    member_ws = FakeWorksheet(
        [
            ["名稱", "同步", "member_id"],
            ["Alice", 300, "alice-id"],
            ["Bob", 301, "bob-id"],
        ],
        title="成員名稱",
    )

    with pytest.raises(ValueError, match="成員.*完整"):
        sync_upload._sync_member_levels_to_worksheet(
            member_ws,
            [
                {"nickname": "Alice", "member_id": "alice-id", "synchro_level": 400},
                {"nickname": nickname, "member_id": "bob-id", "synchro_level": level},
            ],
        )

    assert member_ws.batch_updates == []


def _make_complete_workbook():
    names = ["Alice", "Left One", "Left Two", "Bob", "Left Last"]
    member_ws = FakeWorksheet(
        [["名稱", "同步", "member_id", "Discord", "討論串", "人工註記"]]
        + [[name, 300, f"id-{name}", f"discord-{name}", f"thread-{name}", f"manual-{name}"] for name in names],
        title="成員名稱",
    )
    training_sheets = []
    for element in ("電擊", "水冷", "燃燒", "風壓", "鐵甲", "變化"):
        headers = [["成員", "角色", "資料", "公式"], ["", "等級", "裝備", "總分"]]
        if element == "變化":
            headers.append(["", "變化", "前值", "目前"])
        rows = [[name, 0.125, f"{element}-{name}", "=1+2"] for name in names]
        if element == "水冷":
            rows = [rows[2], rows[3], rows[0], rows[1], rows[4]]
        sheet = FakeWorksheet(headers + rows, title=f"角色練度_{element}")
        for index, row in enumerate(sheet._values[len(headers):], start=len(headers)):
            sheet.row_metadata[index] = {"style": f"style-{row[0]}", "note": f"note-{row[0]}"}
        training_sheets.append(sheet)
    workbook = FakeSpreadsheet([member_ws] + training_sheets)
    return workbook, member_ws, training_sheets


def test_sync_member_levels_cleans_all_seven_sheets_before_adding_new_members():
    workbook, member_ws, training_sheets = _make_complete_workbook()
    original_headers = {sheet.id: deepcopy(sheet._values[:3 if sheet.title.endswith("變化") else 2]) for sheet in training_sheets}
    members = [
        {"nickname": "New One", "member_id": "id-new-one", "synchro_level": 401},
        {"nickname": "Bob Renamed", "member_id": "id-Bob", "synchro_level": 402},
        {"nickname": "Alice", "member_id": "id-Alice", "synchro_level": 403},
        {"nickname": "New Two", "member_id": "id-new-two", "synchro_level": 404},
    ]

    stats = sync_upload._sync_member_levels_to_worksheet(member_ws, members, training_sheets)

    assert stats == {"matched_count": 2, "appended_count": 2, "updated_count": 4, "total_count": 4}
    assert member_ws._values[1:5] == [
        ["Alice", 403, "id-Alice", "discord-Alice", "thread-Alice", "manual-Alice"],
        ["Bob Renamed", 402, "id-Bob", "discord-Bob", "thread-Bob", "manual-Bob"],
        ["New One", 401, "id-new-one", "", "", ""],
        ["New Two", 404, "id-new-two", "", "", ""],
    ]
    for sheet in training_sheets:
        start = 3 if sheet.title.endswith("變化") else 2
        element = sheet.title.removeprefix("角色練度_")
        assert sheet._values[:start] == original_headers[sheet.id]
        assert sheet._values[start:start + 4] == [
            ["Alice", 0.125, f"{element}-Alice", "=1+2"],
            ["Bob Renamed", 0.125, f"{element}-Bob", "=1+2"],
            ["New One", "", "", ""],
            ["New Two", "", "", ""],
        ]
        assert all(not any(row) for row in sheet._values[start + 4:])
        assert sheet.row_metadata[start] == {"style": "style-Alice", "note": "note-Alice"}
        assert sheet.row_metadata[start + 1] == {"style": "style-Bob", "note": "note-Bob"}
        assert "note" not in sheet.row_metadata[start + 2]
        assert "note" not in sheet.row_metadata[start + 3]
    assert all(not any(row) for row in member_ws._values[5:])
    assert len(workbook.batch_requests) == 1
    for request, state in workbook.request_states:
        update = request.get("updateCells", {})
        values = update.get("rows", [{}])[0].get("values", [{}])
        first_value = values[0].get("userEnteredValue", {}).get("stringValue")
        if first_value in {"New One", "New Two"}:
            for rows in state.values():
                assert not any(row[0].startswith("Left ") for row in rows if row and isinstance(row[0], str))


def test_sync_member_levels_shrink_and_retry_preserve_existing_data():
    workbook, member_ws, training_sheets = _make_complete_workbook()
    members = [
        {"nickname": "Bob", "member_id": "id-Bob", "synchro_level": 402},
        {"nickname": "Alice", "member_id": "id-Alice", "synchro_level": 403},
    ]
    sync_upload._sync_member_levels_to_worksheet(member_ws, members, training_sheets)
    after_first_sync = {sheet.id: deepcopy(sheet._values) for sheet in workbook.sheets}
    sync_upload._sync_member_levels_to_worksheet(member_ws, list(reversed(members)), training_sheets)

    assert {sheet.id: sheet._values for sheet in workbook.sheets} == after_first_sync
    assert member_ws._values[1][0] == "Alice"
    assert member_ws._values[2][0] == "Bob"
    assert all(not any(row) for row in member_ws._values[3:])


def test_sync_member_levels_rejected_batch_leaves_all_sheets_unchanged_then_retries():
    workbook, member_ws, training_sheets = _make_complete_workbook()
    original_values = {sheet.id: deepcopy(sheet._values) for sheet in workbook.sheets}
    workbook.rejected_sheet_id = training_sheets[-1].id
    members = [{"nickname": "New", "member_id": "new-id", "synchro_level": 400}]

    with pytest.raises(RuntimeError, match="training sheet rejected update"):
        sync_upload._sync_member_levels_to_worksheet(member_ws, members, training_sheets)
    assert {sheet.id: sheet._values for sheet in workbook.sheets} == original_values

    workbook.rejected_sheet_id = None
    sync_upload._sync_member_levels_to_worksheet(member_ws, members, training_sheets)
    assert member_ws._values[1] == ["New", 400, "new-id", "", "", ""]
    for sheet in training_sheets:
        start = 3 if sheet.title.endswith("變化") else 2
        assert sheet._values[start] == ["New", "", "", ""]
        assert all(not any(row) for row in sheet._values[start + 1:])


def test_sync_member_levels_missing_training_row_does_not_borrow_another_member_data():
    member_ws = FakeWorksheet(
        [["名稱", "同步", "ID"], ["Alice", 300, "alice-id"], ["Bob", 301, "bob-id"]]
    )
    sheet = FakeWorksheet([["成員", "角色"], ["", "欄位"], ["Bob", "bob-data"], ["", ""]], title="角色練度_電擊")
    FakeSpreadsheet([member_ws, sheet])
    sync_upload._sync_member_levels_to_worksheet(
        member_ws,
        [
            {"nickname": "Alice", "member_id": "alice-id", "synchro_level": 400},
            {"nickname": "Bob", "member_id": "bob-id", "synchro_level": 401},
        ],
        [sheet],
    )
    assert sheet._values[2] == ["Alice", "", ""]
    assert sheet._values[3] == ["Bob", "bob-data", ""]


def test_sync_member_levels_same_name_with_changed_id_keeps_existing_data():
    member_ws = FakeWorksheet(
        [["名稱", "同步", "ID", "Discord"], ["Alice", 300, "old-id", "discord-alice"]]
    )
    sheet = FakeWorksheet([["成員", "角色"], ["", "欄位"], ["Alice", "alice-data"]], title="角色練度_電擊")
    FakeSpreadsheet([member_ws, sheet])
    stats = sync_upload._sync_member_levels_to_worksheet(
        member_ws, [{"nickname": "Alice", "member_id": "new-id", "synchro_level": 400}], [sheet]
    )
    assert stats["matched_count"] == 1
    assert stats["appended_count"] == 0
    assert member_ws._values[1] == ["Alice", 400, "new-id", "discord-alice"]
    assert sheet._values[2] == ["Alice", "alice-data", ""]


def test_sync_member_levels_keeps_training_when_display_name_lags_member_sheet():
    member_ws = FakeWorksheet(
        [["名稱", "同步", "ID"], ["Current Name", 300, "same-id"]]
    )
    sheet = FakeWorksheet(
        [["成員", "角色"], ["", "欄位"], ["Previous Name", "existing-training"]],
        title="角色練度_電擊",
    )
    FakeSpreadsheet([member_ws, sheet])

    sync_upload._sync_member_levels_to_worksheet(
        member_ws,
        [{"nickname": "Current Name", "member_id": "same-id", "synchro_level": 400}],
        [sheet],
    )

    assert (sheet._values[2][1], sheet.row_count) == ("existing-training", 3)


def test_sync_member_levels_three_stale_renames_keep_all_training_without_new_rows():
    member_ws = FakeWorksheet(
        [
            ["名稱", "同步", "ID"],
            ["Departing", 300, "departing-id"],
            ["Current One", 301, "one-id"],
            ["Intermediate Two", 302, "two-id"],
            ["Intermediate Three", 303, "three-id"],
            ["Stable", 304, "stable-id"],
        ],
        title="成員名稱",
    )
    sheets = []
    for element in ("電擊", "水冷", "燃燒", "風壓", "鐵甲", "變化"):
        headers = [["成員", "角色", "公式"], ["", "欄位", "合計"]]
        if element == "變化":
            headers.append(["", "變化", "前值"])
        sheets.append(FakeWorksheet(
            headers + [
                ["Departing", "departing-data", ""],
                ["Old One", "one-data", "=1+2"],
                ["Old Two", "two-data", "=2+3"],
                ["Old Three", "three-data", "=3+4"],
                ["Stable", "stable-data", ""],
            ],
            title=f"角色練度_{element}",
        ))
    workbook = FakeSpreadsheet([member_ws] + sheets)
    row_counts = {sheet.id: sheet.row_count for sheet in workbook.sheets}
    sync_upload._sync_member_levels_to_worksheet(
        member_ws,
        [
            {"nickname": "Current One", "member_id": "one-id", "synchro_level": 401},
            {"nickname": "Current Two", "member_id": "two-id", "synchro_level": 402},
            {"nickname": "Current Three", "member_id": "three-id", "synchro_level": 403},
            {"nickname": "Stable", "member_id": "stable-id", "synchro_level": 404},
            {"nickname": "New", "member_id": "new-id", "synchro_level": 405},
        ],
        sheets,
    )
    assert {sheet.id: sheet.row_count for sheet in workbook.sheets} == row_counts
    for sheet in sheets:
        start = 3 if sheet.title.endswith("變化") else 2
        assert sheet._values[start:] == [
            ["Current One", "one-data", "=1+2"],
            ["Current Two", "two-data", "=2+3"],
            ["Current Three", "three-data", "=3+4"],
            ["Stable", "stable-data", ""],
            ["New", "", ""],
        ]
    assert all(
        not set(request).intersection({"insertDimension", "appendDimension", "deleteDimension"})
        for request in workbook.batch_requests[0]["requests"]
    )


def test_sync_member_levels_ambiguous_renamed_row_stops_before_clearing_data():
    member_ws = FakeWorksheet(
        [["名稱", "同步", "ID"], ["Current", 300, "current-id"], ["Bob", 301, "bob-id"]]
    )
    sheet = FakeWorksheet(
        [["成員", "角色"], ["", "欄位"], ["Bob", "bob-data"], ["Unknown Alias", "valuable-data"]],
        title="角色練度_電擊",
    )
    workbook = FakeSpreadsheet([member_ws, sheet])
    before = {ws.id: deepcopy(ws._values) for ws in workbook.sheets}
    with pytest.raises(ValueError, match="無法辨識的原資料"):
        sync_upload._sync_member_levels_to_worksheet(
            member_ws,
            [
                {"nickname": "Current", "member_id": "current-id", "synchro_level": 400},
                {"nickname": "Bob", "member_id": "bob-id", "synchro_level": 401},
            ],
            [sheet],
        )
    assert workbook.batch_requests == []
    assert {ws.id: ws._values for ws in workbook.sheets} == before


def test_sync_member_levels_training_capacity_failure_does_not_change_main_sheet():
    member_ws = FakeWorksheet([["名稱", "同步", "ID"], ["Old", 300, "old-id"], ["", "", ""]])
    sheet = FakeWorksheet([["成員", "角色"], ["", "欄位"], ["Old", "old-data"]], title="角色練度_電擊")
    workbook = FakeSpreadsheet([member_ws, sheet])
    before = {ws.id: deepcopy(ws._values) for ws in workbook.sheets}
    with pytest.raises(ValueError, match="現有資料列不足"):
        sync_upload._sync_member_levels_to_worksheet(
            member_ws,
            [
                {"nickname": "Old", "member_id": "old-id", "synchro_level": 400},
                {"nickname": "New", "member_id": "new-id", "synchro_level": 401},
            ],
            [sheet],
        )
    assert workbook.batch_requests == []
    assert {ws.id: ws._values for ws in workbook.sheets} == before


def test_sync_member_levels_same_names_keep_distinct_ids_and_training_rows():
    member_ws = FakeWorksheet(
        [["名稱", "同步", "ID", "Discord"], ["Same", 300, "id-one", "one"], ["Same", 301, "id-two", "two"]]
    )
    sheet = FakeWorksheet(
        [["成員", "角色"], ["", "欄位"], ["Same", "one-data"], ["Same", "two-data"]], title="角色練度_電擊"
    )
    FakeSpreadsheet([member_ws, sheet])
    sync_upload._sync_member_levels_to_worksheet(
        member_ws,
        [
            {"nickname": "Same", "member_id": "id-two", "synchro_level": 401},
            {"nickname": "Same", "member_id": "id-one", "synchro_level": 400},
        ],
        [sheet],
    )
    assert member_ws._values[1:] == [["Same", 400, "id-one", "one"], ["Same", 401, "id-two", "two"]]
    assert sheet._values[2:] == [["Same", "one-data", ""], ["Same", "two-data", ""]]


def test_sync_member_levels_duplicate_ids_do_not_change_existing_members():
    member_ws = FakeWorksheet([["名稱", "同步", "ID"], ["Old", 300, "old-id"]])
    before = deepcopy(member_ws._values)
    with pytest.raises(ValueError, match="重複 Blabla ID"):
        sync_upload._sync_member_levels_to_worksheet(
            member_ws,
            [
                {"nickname": "One", "member_id": "same-id", "synchro_level": 400},
                {"nickname": "Two", "member_id": "same-id", "synchro_level": 401},
            ],
        )
    assert member_ws._values == before
    assert member_ws.spreadsheet.batch_requests == []


def test_sync_member_levels_writes_names_and_long_ids_as_literal_text():
    member_ws = FakeWorksheet([["名稱", "同步", "ID"], ["", "", ""]])
    nickname = "=IMPORTDATA(\"literal nickname\")"
    member_id = "00012345678901234567890"
    sync_upload._sync_member_levels_to_worksheet(
        member_ws, [{"nickname": nickname, "member_id": member_id, "synchro_level": 400}]
    )
    assert member_ws._values[1] == [nickname, 400, member_id]
    writes = [request["updateCells"] for request in member_ws.spreadsheet.batch_requests[0]["requests"] if "updateCells" in request]
    assert writes[0]["rows"][0]["values"][0]["userEnteredValue"] == {"stringValue": nickname}
    assert writes[0]["rows"][0]["values"][2]["userEnteredValue"] == {"stringValue": member_id}


def _prepare_sync_job(monkeypatch, workbook):
    monkeypatch.setattr(sync_upload, "resolve_account", lambda account: account)
    monkeypatch.setattr(sync_upload, "load_session", lambda account: {"cookies": []})
    monkeypatch.setattr(
        sync_upload, "run_coro_sync",
        lambda factory: ("5631", [{"nickname": "New", "member_id": "new-id", "synchro_level": 400}]),
    )
    monkeypatch.setattr(sync_upload, "resolve_character_sheet_target", lambda guild_id: ("sheet-id", "COA"))
    monkeypatch.setattr(sync_upload, "get_gspread_client", lambda: FakeClient(workbook))


class FakeClient:
    def __init__(self, workbook):
        self.workbook = workbook

    def open_by_key(self, sheet_id):
        assert sheet_id == "sheet-id"
        return self.workbook


def test_sync_upload_job_updates_exactly_five_elements_and_change_sheet(monkeypatch):
    workbook, member_ws, training_sheets = _make_complete_workbook()
    backup = FakeWorksheet([["backup", "untouched", "data"]], title="角色練度_備份")
    workbook.sheets.append(backup)
    _prepare_sync_job(monkeypatch, workbook)
    result = sync_upload._run_sync_upload_single(account="account")

    assert result.ok is True
    assert member_ws._values[1][0] == "New"
    for sheet in training_sheets:
        start = 3 if sheet.title.endswith("變化") else 2
        assert sheet._values[start] == ["New", "", "", ""]
    assert backup._values == [["backup", "untouched", "data"]]


def test_sync_upload_job_missing_change_sheet_does_not_change_any_sheet(monkeypatch):
    workbook, member_ws, _training_sheets = _make_complete_workbook()
    workbook.sheets.pop()
    before = deepcopy(member_ws._values)
    _prepare_sync_job(monkeypatch, workbook)

    result = sync_upload._run_sync_upload_single(account="account")

    assert result.ok is False
    assert "角色練度_變化" in result.message
    assert workbook.batch_requests == []
    assert member_ws._values == before


def test_sync_upload_reports_spreadsheet_permission_error(monkeypatch):
    class FakeClient:
        def open_by_key(self, sheet_id):
            raise PermissionError()

    monkeypatch.setattr(sync_upload, "resolve_account", lambda account: account)
    monkeypatch.setattr(sync_upload, "load_session", lambda account: {"cookies": []})
    monkeypatch.setattr(
        sync_upload,
        "run_coro_sync",
        lambda factory: (
            "5631",
            [{"nickname": "Member", "member_id": "member-1", "synchro_level": 655}],
        ),
    )
    monkeypatch.setattr(
        sync_upload,
        "resolve_character_sheet_target",
        lambda guild_id: ("sheet-id", "COA"),
    )
    monkeypatch.setattr(sync_upload, "get_gspread_client", lambda: FakeClient())

    result = sync_upload._run_sync_upload_single(account="account")

    assert result.ok is False
    assert result.message == "無法存取試算表 COA (sheet-id)：Google 服務帳號沒有權限"
    assert result.outputs == {"sheet_id": "sheet-id", "sheet_name": "COA", "union_id": "5631"}


def test_run_sync_upload_all_outputs_union_summary_and_failures(monkeypatch):
    monkeypatch.setattr(sync_upload, "_resolve_accounts", lambda scope, account: ["ok", "fail", "skip"])

    def fake_single(account=None, worksheet_name="成員名稱"):
        if account == "ok":
            return JobResult(
                ok=True,
                message="上傳完成 (31/32, 新增 1)",
                stats={"updated_count": 31, "total_count": 32},
                outputs={"union_id": "3097", "sheet_name": "月下"},
            )
        if account == "skip":
            return JobResult(ok=False, message="找不到聯盟資訊")
        return JobResult(
            ok=False,
            message="sheet missing",
            stats={"updated_count": 0, "total_count": 32},
            outputs={"union_id": "5010", "sheet_name": "星詠"},
        )

    monkeypatch.setattr(sync_upload, "_run_sync_upload_single", fake_single)

    result = sync_upload.run_sync_upload(scope="all")

    assert result.ok is False
    assert result.message == "\n".join(
        [
            "上傳同步完成 (31/32)",
            "月下 (3097): 31/32",
            "失敗:",
            "- 星詠 (5010) / fail: sheet missing",
            "跳過:",
            "- skip: 找不到聯盟資訊",
        ]
    )
    assert result.stats == {
        "scope": "all",
        "total": 3,
        "ok_count": 1,
        "skipped_count": 1,
        "failed_count": 1,
        "updated_count": 31,
        "total_count": 32,
    }
    assert result.outputs == {
        "_summary_only": True,
        "unions": [
            {
                "account": "ok",
                "union_id": "3097",
                "sheet_name": "月下",
                "updated_count": 31,
                "total_count": 32,
            }
        ],
        "failures": [
            {
                "account": "fail",
                "message": "sheet missing",
                "stats": {"updated_count": 0, "total_count": 32},
                "union_id": "5010",
                "sheet_name": "星詠",
            }
        ],
        "skipped": [{"account": "skip", "message": "找不到聯盟資訊"}],
    }
