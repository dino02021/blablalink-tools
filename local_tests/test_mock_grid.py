from dataclasses import asdict, replace

import pytest

from adapters.mock_grid import materialize_generated_grid
from adapters.mock_sheets import MockSheets
from application.tasks import StateConflict
from domain.view_grid import ViewCell, ViewTab, WorkbookView
from executors.local.demo import run_demo


def make_generated(value="=never()", is_short=False):
    rows = ((ViewCell("Title", "header"), ViewCell()), (ViewCell(value, "failed", "explanation"), ViewCell(50, format_kind="number")))
    if not is_short:
        rows += ((ViewCell("stale"), ViewCell(100, format_kind="number")),)
    tab = ViewTab("scores", "評分", ("name", "score"), tuple(str(i) for i in range(len(rows))), rows,
                  ((0, 1, 0, 2),), 1, 1, (0,))
    view = WorkbookView("config", "training", "calculation", "code", (tab,))
    return {"config_id": view.config_id, "snapshot_id": view.training_id, "calculation_id": view.calculation_id,
            "code_sha": view.code_sha, "view_id": view.view_id, "view": asdict(view)}


def test_materialized_cells_keep_literals_formats_merges_and_dimensions():
    grid = materialize_generated_grid(make_generated())
    tab = grid["tabs"]["scores"]
    assert tab["cells"][1][0]["value"] == {"stringValue": "=never()"}
    assert tab["cells"][1][0]["note"] == "explanation"
    assert tab["cells"][1][0]["foreground_hex"] == "#D93025"
    assert tab["cells"][1][1]["value"] == {"numberValue": 50}
    assert tab["cells"][1][1]["format_kind"] == "number"
    assert (tab["row_count"], tab["column_count"], tab["merges"], tab["frozen_rows"], tab["frozen_columns"], tab["hidden_columns"]) == (3, 2, ((0, 1, 0, 2),), 1, 1, (0,))


def test_replacement_clears_tail_and_preserves_editable_manual_tabs(tmp_path):
    sheets = MockSheets(tmp_path)
    sheets.create("book", "layout", {"draft": "keep"})
    sheets.set_manual_tab("book", "notes", "人工備註", [["old"]])
    sheets.apply("book", "layout", make_generated(), "before")
    sheets.edit_cell("book", "notes", 0, 0, "=literal")
    after = make_generated("new", is_short=True)
    sheets.apply("book", "layout", after, "after")
    book = sheets.read("book")
    assert book["inputs"] == {"draft": "keep"}
    assert book["manual_tabs"]["notes"]["cells"][0][0]["value"] == {"stringValue": "=literal"}
    assert len(book["generated_grid"]["tabs"]["scores"]["cells"]) == 2
    assert sheets.observe("book", after, "after") == "after"
    with pytest.raises(StateConflict, match="protected"):
        sheets.edit_cell("book", "scores", 1, 1, 0)
    with pytest.raises(StateConflict):
        sheets.set_manual_tab("book", "scores", "別名", [["replace"]])


@pytest.mark.parametrize("damage", ["value", "color", "merge", "protection", "frozen", "hidden"])
def test_grid_tampering_cannot_be_certified_by_unchanged_payload_marker(tmp_path, damage):
    sheets = MockSheets(tmp_path)
    sheets.create("book", "layout", {})
    generated = make_generated()
    sheets.apply("book", "layout", generated, "commit")
    book = sheets.read("book")
    tab = book["generated_grid"]["tabs"]["scores"]
    if damage == "value":
        tab["cells"][1][1]["value"] = {"numberValue": 999}
    elif damage == "color":
        tab["cells"][1][0]["foreground_hex"] = "#000000"
    elif damage == "merge":
        tab["merges"] = []
    elif damage == "protection":
        tab["is_generated_protected"] = False
    elif damage == "frozen":
        tab["frozen_rows"] = 0
    else:
        tab["hidden_columns"] = []
    sheets._save("book", book)  # deliberate external mutation beyond the protected edit API
    assert sheets.observe("book", generated, "commit") is None
    with pytest.raises(StateConflict, match="content"):
        sheets.apply("book", "layout", generated, "commit")


@pytest.mark.parametrize("collision", ["key", "title"])
def test_generated_initialization_never_replaces_conflicting_manual_tab(tmp_path, collision):
    sheets = MockSheets(tmp_path)
    sheets.create("book", "layout", {})
    sheets.set_manual_tab("book", "scores" if collision == "key" else "manual", "評分" if collision == "title" else "人工", [["keep"]])
    before = sheets.read("book")
    with pytest.raises(StateConflict, match="manual"):
        sheets.apply("book", "layout", make_generated(), "commit")
    assert sheets.read("book") == before


def test_structural_change_requires_explicit_migration_and_keeps_previous_grid(tmp_path):
    sheets = MockSheets(tmp_path)
    sheets.create("book", "layout", {})
    before = make_generated()
    sheets.apply("book", "layout", before, "old")
    from domain.view_grid import restore_workbook_view
    view = restore_workbook_view(before["view"])
    changed = replace(view, tabs=(replace(view.tabs[0], hidden_columns=()),))
    after = {**before, "view": asdict(changed), "view_id": changed.view_id}
    with pytest.raises(StateConflict, match="migration"):
        sheets.apply("book", "layout", after, "new")
    with pytest.raises(StateConflict, match="migration"):
        sheets.apply("book", "layout", {}, "bypass")
    assert sheets.observe("book", before, "old") == "old"


def test_source_binding_and_merge_notes_cannot_silently_differ():
    generated = make_generated()
    with pytest.raises(ValueError, match="binding"):
        materialize_generated_grid({**generated, "snapshot_id": "other"})
    with pytest.raises(ValueError, match="hides"):
        ViewTab("tab", "title", ("a", "b"), ("row",), ((ViewCell("title"), ViewCell(note="hidden")),), ((0, 1, 0, 2),))


def test_demo_publishes_eight_physical_generated_tabs(tmp_path):
    run_demo(tmp_path / "demo")
    sheets = MockSheets(tmp_path / "demo/workbooks")
    book = sheets.read("output_demo")
    assert len(book["generated_grid"]["tabs"]) == 8
    assert sheets.observe("output_demo", book["generated"], book["commit_id"]) == book["commit_id"]
