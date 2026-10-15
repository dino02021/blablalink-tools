from dataclasses import replace
from contextlib import closing
import json

import pytest

from adapters.legacy_migration import prepare_legacy_migration
from adapters.legacy_standard import HEADERS, LegacyCatalog, read_legacy_standard
from adapters.mock_sheets import MockSheets
from adapters.local_state import LocalStateStore
from adapters.sheet_standard import StandardReference
from application.tasks import StateConflict
from application.sheet_healthcheck import reconcile_legacy_standard
from domain.snapshots import read_config


@pytest.fixture
def legacy(tmp_path):
    grid = [[None]*23 for _ in range(6)]
    checklist, merges = [list(HEADERS)], []
    for index, element in enumerate(("電擊", "燃燒")):
        start = 1+11*index
        name = f"mock_{index}"
        grid[0][start], grid[1][start] = element, name
        for offset, label in ((0, "技能"), (3, "裝備 (頭/手/身/腳)"), (7, "收藏品"), (8, "詞條 (攻)")):
            grid[2][start+offset] = label
        for offset, threshold in ((0, 5), (3, 10), (7, 2), (8, 3)):
            grid[3][start+offset], grid[4][start+offset] = threshold, 1
        grid[5][start] = 2
        merges.extend([[row, row+1, start, start+11] for row in (0, 1, 5)])
        merges.extend([[2, 3, start+a, start+b] for a, b in ((0, 3), (3, 7), (8, 11))])
        checklist.append([name, element, "輸出", "攻擊力", "", "", "追蹤", "=literal nickname", ""])
    inputs = {"legacy_checklist": checklist, "legacy_standard": grid, "standard_column_count": 23,
        "standard_role_ids": {"2": "role_a", "13": "role_b"}, "standard_merges": merges,
        "standard_cell_notes": {"0,1": "element", "2,1": "skills", "3,1": "threshold", "5,1": "weight"},
        "manual_note": "keep"}
    reference = StandardReference("reference", ("role_a", "role_b"), ("attack",))
    catalog = LegacyCatalog(reference, (("mock_0", "role_a"), ("mock_1", "role_b")), (("攻擊力", "attack"),))
    sheets = MockSheets(tmp_path)
    sheets.create("book", "old_layout", inputs)
    return sheets, catalog


def make_plan(sheets, catalog):
    return prepare_legacy_migration("book", sheets.read("book"), catalog, "common", "2026-10-15T00:00:00Z", "code")


def test_preview_maps_every_old_cell_and_introduces_blank_breakthrough(legacy):
    sheets, catalog = legacy
    before = sheets.read("book")
    plan = make_plan(sheets, catalog)
    target = json.loads(plan.target_inputs_json)
    assert sheets.read("book") == before
    assert plan.inserted_columns == (1, 13) and plan.column_map[-1] == (22, 24)
    assert target["standard_column_count"] == 25
    assert target["standard_role_ids"] == {"2": "role_a", "14": "role_b"}
    for row in range(6):
        for old, new in plan.column_map:
            actual = new-1 if row in (0, 1, 5) and old in (1, 12) else new
            assert target["legacy_standard"][row][actual] == before["inputs"]["legacy_standard"][row][old]
    for start in plan.inserted_columns:
        assert target["legacy_standard"][2][start] == "突破"
        assert target["legacy_standard"][3][start] is None
        assert target["legacy_standard"][4][start] is None
    document = read_legacy_standard({"inputs": target}, "common", plan.captured_at, catalog)
    assert document.snapshot_id == plan.config_id
    rules, labels = read_config(document)
    for rule in rules:
        metrics = {metric.metric_id: metric for metric in rule.metrics}
        assert metrics["skill_1"].threshold == 5 and metrics["equip_head"].threshold == 10
        assert metrics["collection"].threshold == 2 and metrics["trait_1"].threshold == 3
        assert metrics["breakthrough"].threshold == ""
    assert labels[0].display_name == "=literal nickname"


def test_merge_and_note_coordinates_keep_their_semantic_anchor(legacy):
    sheets, catalog = legacy
    target = json.loads(make_plan(sheets, catalog).target_inputs_json)
    assert [0, 1, 1, 13] in target["standard_merges"]
    assert [2, 3, 2, 5] in target["standard_merges"]
    assert [2, 3, 5, 9] in target["standard_merges"]
    assert [2, 3, 10, 13] in target["standard_merges"]
    assert target["standard_cell_notes"] == {"0,1": "element", "2,2": "skills", "3,2": "threshold", "5,1": "weight"}
    assert len(set(target["standard_column_ids"])) == 25


def test_confirmed_migration_changes_draft_only_and_preserves_full_archive(legacy):
    sheets, catalog = legacy
    plan = make_plan(sheets, catalog)
    original = sheets.read("book")
    inputs = original["inputs"]
    inputs["manual_note"] = "collaborator"
    sheets.edit_inputs("book", inputs)
    revision = sheets.apply_legacy_migration(plan, catalog, plan.plan_id, plan.config_id, "code")
    book = sheets.read("book")
    assert revision == plan.target_layout_revision and revision != "old_layout"
    assert book["inputs"]["manual_note"] == "collaborator"
    assert book["generated"] == {} and book["commit_id"] is None
    archived = book["legacy_standard_archives"][0]
    assert archived["inputs"]["legacy_standard"] == original["inputs"]["legacy_standard"]
    assert archived["inputs"]["standard_merges"] == original["inputs"]["standard_merges"]
    sheets.apply_legacy_migration(plan, catalog, plan.plan_id, plan.config_id, "code")
    assert sheets.read("book") == book


@pytest.mark.parametrize("change", ["threshold", "checklist", "role_ids", "notes", "merge", "layout", "catalog", "code", "config", "confirmation", "forged"])
def test_changed_source_or_dependency_cannot_use_the_previous_plan(legacy, change):
    sheets, catalog = legacy
    plan = make_plan(sheets, catalog)
    book = sheets.read("book")
    if change == "threshold":
        book["inputs"]["legacy_standard"][3][1] = 6
    elif change == "checklist":
        book["inputs"]["legacy_checklist"][1][7] = "changed"
    elif change == "role_ids":
        book["inputs"]["standard_role_ids"]["2"] = "role_b"
    elif change == "notes":
        book["inputs"]["standard_cell_notes"]["3,1"] = "changed"
    elif change == "merge":
        book["inputs"]["standard_merges"].pop()
    elif change == "layout":
        book["layout_revision"] = "changed"
    elif change == "catalog":
        catalog = replace(catalog, reference=replace(catalog.reference, version="new_reference"))
    elif change == "forged":
        plan = replace(plan, column_map=())
    sheets._save("book", book)
    with pytest.raises((ValueError, StateConflict)):
        sheets.apply_legacy_migration(plan, catalog, "wrong" if change == "confirmation" else plan.plan_id,
            "wrong" if change == "config" else plan.config_id, "wrong" if change == "code" else "code")
    assert sheets.read("book") == book


@pytest.mark.parametrize("damage", ["width", "role_id_missing", "merge_inventory_missing", "notes_missing", "hidden_value", "hidden_note", "merge_overlap", "cross_role", "source_identity"])
def test_unknown_or_destructive_legacy_shape_is_rejected(legacy, damage):
    sheets, catalog = legacy
    book = sheets.read("book")
    inputs = book["inputs"]
    if damage == "width":
        inputs["standard_column_count"] = 24
    elif damage == "role_id_missing":
        inputs["standard_role_ids"].pop("2")
    elif damage == "merge_inventory_missing":
        inputs.pop("standard_merges")
    elif damage == "notes_missing":
        inputs.pop("standard_cell_notes")
    elif damage == "hidden_value":
        inputs["legacy_standard"][0][2] = "do not lose"
    elif damage == "hidden_note":
        inputs["standard_cell_notes"]["0,2"] = "do not lose"
    elif damage == "merge_overlap":
        inputs["standard_merges"].append(inputs["standard_merges"][0])
    elif damage == "cross_role":
        inputs["standard_merges"][1][3] = 23
    else:
        inputs["standard_column_ids"] = ["wrong"]*23
    sheets._save("book", book)
    with pytest.raises(ValueError):
        make_plan(sheets, catalog)


def test_replay_never_overwrites_later_draft_edits(legacy):
    sheets, catalog = legacy
    plan = make_plan(sheets, catalog)
    sheets.apply_legacy_migration(plan, catalog, plan.plan_id, plan.config_id, "code")
    inputs = sheets.read("book")["inputs"]
    inputs["legacy_standard"][3][2] = 9
    sheets.edit_inputs("book", inputs)
    with pytest.raises(StateConflict, match="replay"):
        sheets.apply_legacy_migration(plan, catalog, plan.plan_id, plan.config_id, "code")
    assert sheets.read("book")["inputs"]["legacy_standard"][3][2] == 9


def test_migrated_draft_can_only_prepare_the_reviewed_config_candidate(legacy, tmp_path):
    sheets, catalog = legacy
    plan = make_plan(sheets, catalog)
    sheets.apply_legacy_migration(plan, catalog, plan.plan_id, plan.config_id, "code")
    with closing(LocalStateStore(tmp_path / "state.sqlite")) as store:
        candidate = reconcile_legacy_standard(store, sheets, "book", "common", plan.captured_at, catalog)
        assert store.candidate_document(candidate).snapshot_id == plan.config_id
        assert store.config_head("healthcheck", "common") is None
        assert not store.list_jobs()


def test_legacy_archive_capacity_never_discards_older_sources(legacy):
    sheets, catalog = legacy
    plan = make_plan(sheets, catalog)
    book = sheets.read("book")
    book["legacy_standard_archives"] = [{"plan_id": f"old_{index}"} for index in range(20)]
    sheets._save("book", book)
    with pytest.raises(StateConflict, match="capacity"):
        sheets.apply_legacy_migration(plan, catalog, plan.plan_id, plan.config_id, "code")
    assert sheets.read("book") == book
