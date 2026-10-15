from dataclasses import replace

import pytest

from adapters.mock_sheets import MockSheets
from domain.layout import Column, Layout, confirm_layout, prepare_layout, render_columns


def test_plan_archives_appends_and_restores_without_moving_human_columns():
    current = Layout("book_demo", 10, (Column("role_a:skill", "原名"), Column("note", "人工", "human"), Column("role_b:skill", "舊角色")))
    plan = prepare_layout(current, (Column("role_a:skill", "新暱稱"), Column("role_c:trait:42", "新詞條")), "config_1", "code_1")
    assert plan.changes == (("update", "role_a:skill", 0), ("archive", "role_b:skill", 2), ("append", "role_c:trait:42", 3))
    after = confirm_layout(plan, current, plan.plan_id, "config_1", "code_1")
    assert after.columns[1] == current.columns[1]
    restored = prepare_layout(after, (Column("role_b:skill", "恢復角色"),), "config_2", "code_1")
    assert restored.after.columns[2].is_archived is False
    assert len(restored.after.columns) == 4


@pytest.mark.parametrize("change", ["plan", "base", "config", "code", "forged"])
def test_layout_confirmation_is_bound_to_exact_plan_and_dependencies(change):
    current = Layout("book_demo", 1, (Column("score", "分數"), Column("human", "人工", "human")))
    plan = prepare_layout(current, (Column("score", "新分數"),), "config", "code")
    confirmed, config, code = plan.plan_id, "config", "code"
    if change == "plan":
        confirmed = "different"
    elif change == "base":
        current = replace(current, tab_id=2)
    elif change == "config":
        config = "changed"
    elif change == "code":
        code = "changed"
    else:
        plan = replace(plan, after=replace(plan.after, columns=(Column("score", "新分數"), Column("human", "被覆寫", "human"))))
        confirmed = plan.plan_id
    with pytest.raises(ValueError):
        confirm_layout(plan, current, confirmed, config, code)


def test_system_identity_cannot_take_human_column():
    current = Layout("book_demo", 1, (Column("manual", "人工", "human"),))
    with pytest.raises(ValueError):
        prepare_layout(current, (Column("manual", "系統"),), "config", "code")


def test_literal_cells_status_formats_and_trailing_rows():
    layout = Layout("book_demo", 7, (Column("score", "分數", format_kind="number"), Column("retired", "封存", is_archived=True)))
    requests = render_columns(layout, ("area:member_a", "area:member_b"),
        {("area:member_a", "score"): "=IMPORTXML(\"mock\")", ("area:member_b", "score"): 0},
        {("area:member_b", "score"): "fail"}, previous_member_count=3)
    assert len(requests) == 1
    update = requests[0]["updateCells"]
    assert update["range"]["sheetId"] == 7 and update["range"]["endRowIndex"] == 4
    assert update["rows"][1]["values"][0]["userEnteredValue"] == {"stringValue": "=IMPORTXML(\"mock\")"}
    assert update["rows"][2]["values"][0]["userEnteredValue"] == {"numberValue": 0}
    assert update["rows"][2]["values"][0]["note"] == "fail"
    assert update["rows"][3]["values"][0]["userEnteredValue"] == {}


def test_mixed_human_columns_require_stable_member_rows():
    layout = Layout("book_demo", 1, (Column("score", "分數"), Column("note", "人工", "human")))
    values = {("member_a", "score"): 1, ("member_b", "score"): 2}
    with pytest.raises(ValueError):
        render_columns(layout, ("member_b", "member_a"), values, existing_member_ids=("member_a", "member_b"))
    requests = render_columns(layout, ("member_a", "member_b"), values, existing_member_ids=("member_a", "member_b"))
    assert [r["updateCells"]["range"]["startColumnIndex"] for r in requests] == [0]


def test_mock_confirmed_layout_keeps_archive_and_human_data(tmp_path):
    sheets = MockSheets(tmp_path / "books")
    sheets.create("book_demo", "legacy", {"member_demo": {"note": "保留"}})
    sheets.apply("book_demo", "legacy", {"old_values": [[1], [2]]}, "old_commit")
    current = Layout("book_demo", 3, (Column("role_a", "角色 A"),))
    sheets.initialize_layout(current)
    plan = prepare_layout(current, (Column("role_b", "角色 B"),), "config", "code")
    with pytest.raises(ValueError):
        sheets.apply_layout(plan, "unconfirmed", "config", "code")
    assert sheets.read_layout("book_demo") == current
    sheets.apply_layout(plan, plan.plan_id, "config", "code")
    book = sheets.read("book_demo")
    assert book["inputs"] == {"member_demo": {"note": "保留"}}
    assert book["layout_archives"][0]["generated"] == {"old_values": [[1], [2]]}
    assert sheets.read_layout("book_demo") == plan.after


@pytest.mark.parametrize("value", [float("nan"), float("inf"), {}, [], 2 ** 53 + 1, 10 ** 1000])
def test_invalid_cell_values_cannot_enter_request(value):
    layout = Layout("book_demo", 1, (Column("score", "分數"),))
    with pytest.raises(ValueError):
        render_columns(layout, ("member",), {("member", "score"): value})
