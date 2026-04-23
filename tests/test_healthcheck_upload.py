# -*- coding: utf-8 -*-

import pytest

from lib.jobs import healthcheck_upload


class FakeSpreadsheet:
    def __init__(self):
        self.batch_calls = []
        self._worksheets = {}

    def worksheet(self, name):
        return self._worksheets[name]

    def batch_update(self, payload):
        self.batch_calls.append(payload)


class FakeWorksheet:
    def __init__(self, spreadsheet, title, values, *, sheet_id=1, col_count=200):
        self.spreadsheet = spreadsheet
        self.title = title
        self.id = sheet_id
        self._values = [list(row) for row in values]
        self.col_count = col_count
        self.row_count = max(len(values), 1)
        self.updates = []
        spreadsheet._worksheets[title] = self

    def get_all_values(self):
        return [list(row) for row in self._values]

    def row_values(self, row):
        if 1 <= row <= len(self._values):
            return list(self._values[row - 1])
        return []

    def add_cols(self, count):
        self.col_count += count

    def update(self, cell_range, values):
        self.updates.append((cell_range, values))

    def update_cell(self, row, col, value):
        while len(self._values) < row:
            self._values.append([])
        while len(self._values[row - 1]) < col:
            self._values[row - 1].append("")
        self._values[row - 1][col - 1] = value

    def batch_clear(self, ranges):
        return None

    def delete_rows(self, start, end):
        return None


def _block_name_row(names):
    row = [""]
    for name in names:
        row.extend([name] + [""] * (healthcheck_upload.TEMPLATE_BLOCK_WIDTH - 1))
    return row


def _block_group_row(count):
    row = [""]
    for _ in range(count):
        row.extend(healthcheck_upload.STANDARD_ROW2_VALUES)
    return row


def _change_element_row(element, count):
    return ["", element] + [""] * (count * healthcheck_upload.TEMPLATE_BLOCK_WIDTH - 1)


def _flatten_requests(batch_calls):
    requests = []
    for payload in batch_calls:
        requests.extend(payload.get("requests", []))
    return requests


def _background_colors(requests):
    colors = []
    for req in requests:
        repeat_cell = req.get("repeatCell")
        if not repeat_cell:
            continue
        color = (
            repeat_cell.get("cell", {})
            .get("userEnteredFormat", {})
            .get("backgroundColor")
        )
        if color is not None:
            colors.append(color)
    return colors


def test_header_background_is_target_color():
    assert healthcheck_upload.HEADER_BG == {"red": 247 / 255, "green": 203 / 255, "blue": 77 / 255}


def test_build_fixed_width_insert_actions_inserts_after_previous():
    actions = healthcheck_upload._build_fixed_width_insert_actions(
        ["A", "B", "C"],
        ["A", "C"],
        [
            {"start": 2, "end": 12, "width": 11},
            {"start": 13, "end": 23, "width": 11},
        ],
        2,
        sheet_name="test",
        element="Fire",
    )

    assert actions == [{"name": "B", "previous": "A", "insert_col": 13}]


def test_raise_previous_not_found_includes_context():
    with pytest.raises(ValueError, match="name=C") as exc_info:
        healthcheck_upload._raise_previous_not_found(
            "test",
            "C",
            "B",
            member_type="輸出",
            element="電擊",
        )

    assert "previous=B" in str(exc_info.value)


def test_build_score_result_insert_actions_uses_previous_label():
    actions = healthcheck_upload._build_score_result_insert_actions(
        [
            {"name": "A", "nickname": "NickA", "type": "輸出", "element": "電擊"},
            {"name": "B", "nickname": "NickB", "type": "輸出", "element": "電擊"},
            {"name": "C", "nickname": "NickC", "type": "輸出", "element": "電擊"},
        ],
        [
            {"col": 4, "label": "NickA"},
            {"col": 5, "label": "NickC"},
        ],
        6,
        7,
        sheet_name="score",
        member_type="輸出",
        element="電擊",
    )

    assert actions == [{"label": "NickB", "previous": "NickA", "insert_col": 5, "template_col": 4}]


def test_build_structural_sync_plan_groups_entries():
    plan = healthcheck_upload._build_structural_sync_plan(
        [
            {"name": "A", "nickname": "NA", "type": "輸出", "element": "電擊"},
            {"name": "B", "nickname": "NB", "type": "輸出", "element": "電擊"},
            {"name": "C", "nickname": "NC", "type": "輔助", "element": "水冷"},
        ]
    )

    assert plan["names_by_element"] == {"電擊": ["A", "B"], "水冷": ["C"]}
    assert plan["name_to_element"] == {"A": "電擊", "B": "電擊", "C": "水冷"}
    assert plan["score_entries_by_key"][healthcheck_upload._build_group_key("輸出", "電擊")] == [
        {"name": "A", "nickname": "NA", "type": "輸出", "element": "電擊"},
        {"name": "B", "nickname": "NB", "type": "輸出", "element": "電擊"},
    ]


def test_sync_training_element_sheet_batches_template_requests_and_uses_new_borders():
    spreadsheet = FakeSpreadsheet()
    element = healthcheck_upload.HEALTHCHECK_ELEMENT_ORDER[0]
    FakeWorksheet(
        spreadsheet,
        f"{healthcheck_upload.TRAINING_SHEET_PREFIX}{element}",
        [
            _block_name_row(["A", "C"]),
            _block_group_row(2),
            [""] * 23,
        ],
    )

    inserted = healthcheck_upload._sync_training_element_sheet(spreadsheet, element, ["A", "B", "C"])

    assert inserted == 1
    assert len(spreadsheet.batch_calls) == 2
    requests = _flatten_requests(spreadsheet.batch_calls[1:])
    border_request = next(
        req["updateBorders"]
        for req in requests
        if "updateBorders" in req and req["updateBorders"].get("top", {}).get("style") == "SOLID_MEDIUM"
    )
    assert border_request["top"]["style"] == "SOLID_MEDIUM"
    assert border_request["top"]["color"] == healthcheck_upload.BORDER_COLOR
    divider_ranges = [
        req["updateBorders"]["range"]
        for req in requests
        if "updateBorders" in req and req["updateBorders"].get("left", {}).get("style") == "SOLID"
    ]
    assert {"sheetId": 1, "startRowIndex": 0, "endRowIndex": 3, "startColumnIndex": 15, "endColumnIndex": 16} in divider_ranges
    assert all("innerVertical" not in req.get("updateBorders", {}) for req in requests if "updateBorders" in req)
    assert healthcheck_upload.DATA_BG_ODD not in _background_colors(requests)
    assert healthcheck_upload.DATA_BG_EVEN not in _background_colors(requests)


def test_sync_training_change_sheet_expands_existing_element_group():
    spreadsheet = FakeSpreadsheet()
    element = healthcheck_upload.HEALTHCHECK_ELEMENT_ORDER[0]
    FakeWorksheet(
        spreadsheet,
        healthcheck_upload.TRAINING_CHANGE_WORKSHEET,
        [
            _change_element_row(element, 2),
            _block_name_row(["A", "C"]),
            _block_group_row(2),
            [""] * 23,
        ],
    )

    inserted = healthcheck_upload._sync_training_change_sheet(spreadsheet, {element: ["A", "B", "C"]})

    assert inserted == 1
    assert len(spreadsheet.batch_calls) == 2
    requests = _flatten_requests(spreadsheet.batch_calls[1:])
    unmerge_ranges = [req["unmergeCells"]["range"] for req in requests if "unmergeCells" in req]
    merge_ranges = [req["mergeCells"]["range"] for req in requests if "mergeCells" in req]
    assert {"sheetId": 1, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 1, "endColumnIndex": 23} in unmerge_ranges
    assert {"sheetId": 1, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 1, "endColumnIndex": 34} in merge_ranges
    block_borders = [
        req["updateBorders"]
        for req in requests
        if "updateBorders" in req and req["updateBorders"]["range"].get("startRowIndex") == 1
    ]
    assert block_borders
    assert all("top" not in border for border in block_borders)


def test_sync_training_change_sheet_raises_when_element_group_missing():
    spreadsheet = FakeSpreadsheet()
    element = healthcheck_upload.HEALTHCHECK_ELEMENT_ORDER[0]
    FakeWorksheet(
        spreadsheet,
        healthcheck_upload.TRAINING_CHANGE_WORKSHEET,
        [
            [""] * 23,
            _block_name_row(["A", "C"]),
            _block_group_row(2),
            [""] * 23,
        ],
    )

    with pytest.raises(ValueError, match=element):
        healthcheck_upload._sync_training_change_sheet(spreadsheet, {element: ["A", "B", "C"]})


def test_sync_standard_sheet_batches_all_inserts():
    spreadsheet = FakeSpreadsheet()
    fire = healthcheck_upload.HEALTHCHECK_ELEMENT_ORDER[0]
    water = healthcheck_upload.HEALTHCHECK_ELEMENT_ORDER[1]
    FakeWorksheet(
        spreadsheet,
        healthcheck_upload.STANDARD_WORKSHEET,
        [
            _block_name_row(["A", "C", "D", "F"]),
            _block_group_row(4),
            [""] * 45,
            [""] * 45,
            [""] * 45,
        ],
    )

    inserted = healthcheck_upload._sync_standard_sheet(
        spreadsheet,
        {fire: ["A", "B", "C"], water: ["D", "E", "F"]},
        {"A": fire, "B": fire, "C": fire, "D": water, "E": water, "F": water},
    )

    assert inserted == 2
    assert len(spreadsheet.batch_calls) == 2
    requests = _flatten_requests(spreadsheet.batch_calls[1:])
    border_requests = [
        req["updateBorders"]
        for req in requests
        if "updateBorders" in req and req["updateBorders"].get("top", {}).get("style") == "SOLID_MEDIUM"
    ]
    assert border_requests
    assert all(req["top"]["color"] == healthcheck_upload.BORDER_COLOR for req in border_requests)
    assert all("innerVertical" not in req for req in border_requests)
    assert healthcheck_upload.DATA_BG_ODD not in _background_colors(requests)
    assert healthcheck_upload.DATA_BG_EVEN not in _background_colors(requests)
    merge_ranges = [req["mergeCells"]["range"] for req in requests if "mergeCells" in req]
    assert {"sheetId": 1, "startRowIndex": 4, "endRowIndex": 5, "startColumnIndex": 12, "endColumnIndex": 23} in merge_ranges
    row5_updates = [
        req["updateCells"]
        for req in requests
        if "updateCells" in req and req["updateCells"]["range"]["startRowIndex"] == 4
    ]
    assert row5_updates
    assert row5_updates[0]["rows"][0]["values"][0]["userEnteredValue"]["stringValue"] == ""


def test_sync_healthcheck_sheet_fills_blank_nickname_in_batch_update():
    spreadsheet = FakeSpreadsheet()
    ws = FakeWorksheet(
        spreadsheet,
        healthcheck_upload.HEALTHCHECK_WORKSHEET,
        [
            healthcheck_upload.HEALTHCHECK_HEADERS,
            ["Alice", "電擊", "輸出", "", ""],
        ],
    )

    synced = healthcheck_upload._sync_healthcheck_sheet(
        spreadsheet,
        [{"name": "Alice", "element": "電擊", "type": "輸出"}],
    )

    assert synced[0]["nickname"] == "Alice"
    data_update = ws.updates[1]
    assert data_update[1][0][3] == "Alice"


def test_sync_score_result_columns_does_not_emit_copy_paste_requests():
    spreadsheet = FakeSpreadsheet()
    sheet = FakeWorksheet(
        spreadsheet,
        healthcheck_upload.SCORE_RESULT_WORKSHEET,
        [
            ["", "", "", "輸出", "", ""],
            ["", "", "", "電擊", "", ""],
            ["", "", "", "NickA", "總分", "排名"],
            ["", "", "", "", "", ""],
        ],
    )

    inserted = healthcheck_upload._sync_score_result_columns(
        spreadsheet,
        {
            healthcheck_upload._build_group_key("輸出", "電擊"): [
                {"name": "A", "nickname": "NickA", "type": "輸出", "element": "電擊"},
                {"name": "B", "nickname": "NickB", "type": "輸出", "element": "電擊"},
            ]
        },
    )

    assert inserted == 1
    requests = _flatten_requests(spreadsheet.batch_calls)
    assert all("copyPaste" not in req for req in requests)
    update_request = next(req["updateCells"] for req in requests if "updateCells" in req)
    assert update_request["range"]["startRowIndex"] == 2
    assert update_request["range"]["startColumnIndex"] == 4
