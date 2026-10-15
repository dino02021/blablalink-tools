from dataclasses import asdict, replace
from contextlib import closing
import json

import pytest

from adapters.mock_sheets import MockSheets
from adapters.local_state import LocalStateStore
from application.publication import publish_workbooks
from application.tasks import StateConflict
from application.tasks import TaskSpec
from domain.view_grid import ViewCell, ViewTab, WorkbookView, restore_workbook_view
from domain.snapshots import canonical_json


@pytest.fixture
def migration(tmp_path):
    tab = ViewTab("score", "Score", ("name", "score"), ("member",), ((ViewCell("mock"), ViewCell(50)),))
    view = WorkbookView("config", "training", "calculation", "code", (tab,))
    before = {"view": asdict(view), "view_id": view.view_id, "config_id": view.config_id,
        "snapshot_id": view.training_id, "calculation_id": view.calculation_id, "code_sha": view.code_sha}
    changed = replace(view, tabs=(replace(tab, hidden_columns=(0,)), ViewTab("report", "Report", ("text",), ("member",), ((ViewCell("report"),),))))
    after = {**before, "view": asdict(changed), "view_id": changed.view_id}
    before, after = json.loads(canonical_json(before)), json.loads(canonical_json(after))
    sheets = MockSheets(tmp_path)
    sheets.create("book", "layout", {"draft": "keep"})
    sheets.set_manual_tab("book", "notes", "Notes", [["human"]])
    sheets.apply("book", "layout", before, "old")
    plan = sheets.plan_grid_migration("book", after)
    return sheets, before, after, plan


def test_preview_confirmation_archives_only_when_publication_applies(migration):
    sheets, before, after, plan = migration
    assert plan.changes == (("add", "report"), ("replace", "score"))
    assert sheets.read("book")["generated"] == before
    sheets.approve_grid_migration(plan, after, plan.plan_id, "config", "code")
    assert sheets.read("book")["generated"] == before
    assert "grid_archives" not in sheets.read("book")
    sheets.edit_cell("book", "notes", 0, 0, "collaborator")
    sheets.apply("book", "layout", after, "new")
    book = sheets.read("book")
    assert sheets.observe("book", after, "new") == "new"
    assert book["grid_archives"][0]["generated"] == before
    assert book["grid_archives"][0]["generated_grid"]["view_id"] == before["view_id"]
    assert book["grid_archives"][0]["plan_id"] == plan.plan_id
    assert book["manual_tabs"]["notes"]["cells"][0][0]["value"] == {"stringValue": "collaborator"}
    assert book["inputs"] == {"draft": "keep"} and "grid_migration_approval" not in book
    sheets.apply("book", "layout", after, "new")
    assert len(sheets.read("book")["grid_archives"]) == 1


@pytest.mark.parametrize("substitution", ["confirmation", "config", "code", "plan", "payload"])
def test_confirmation_cannot_substitute_reviewed_dependencies(migration, substitution):
    sheets, before, after, plan = migration
    reviewed = replace(plan, changes=()) if substitution == "plan" else plan
    target = {**after, "extra": "changed"} if substitution == "payload" else after
    with pytest.raises(StateConflict):
        sheets.approve_grid_migration(reviewed, target, "wrong" if substitution == "confirmation" else reviewed.plan_id,
            "wrong" if substitution == "config" else "config", "wrong" if substitution == "code" else "code")
    assert sheets.read("book")["generated"] == before
    assert "grid_migration_approval" not in sheets.read("book")


@pytest.mark.parametrize("mutation", ["payload", "base", "layout", "grid", "manual_collision"])
def test_approval_is_revalidated_before_the_atomic_write(migration, mutation):
    sheets, before, after, plan = migration
    sheets.approve_grid_migration(plan, after, plan.plan_id, "config", "code")
    target = after
    if mutation == "payload":
        target = {**after, "extra": "changed"}
    elif mutation == "base":
        sheets.apply("book", "layout", before, "newer_base")
    elif mutation == "manual_collision":
        sheets.set_manual_tab("book", "human_report", "Report", [["do not overwrite"]])
    else:
        book = sheets.read("book")
        if mutation == "layout":
            book["layout_revision"] = "new_layout"
        else:
            book["generated_grid"]["tabs"]["score"]["cells"][0][0]["value"] = {"stringValue": "corrupt"}
        sheets._save("book", book)
    pre_write = sheets.read("book")
    with pytest.raises((StateConflict, ValueError)):
        sheets.apply("book", "layout", target, "new")
    assert sheets.read("book") == pre_write


@pytest.mark.parametrize("fault", ["before", "after"])
def test_timeout_preserves_atomic_approval_archive_and_replay(migration, fault):
    sheets, before, after, plan = migration
    sheets.approve_grid_migration(plan, after, plan.plan_id, "config", "code")
    with pytest.raises(TimeoutError):
        sheets.apply("book", "layout", after, "new", fault=fault)
    restarted = MockSheets(sheets.directory)
    if fault == "before":
        assert restarted.observe("book", before, "old") == "old"
        assert "grid_archives" not in restarted.read("book")
    else:
        assert restarted.observe("book", after, "new") == "new"
    restarted.apply("book", "layout", after, "new")
    assert len(restarted.read("book")["grid_archives"]) == 1


def test_archive_capacity_stops_migration_without_deleting_history(migration):
    sheets, before, after, plan = migration
    book = sheets.read("book")
    book["grid_archives"] = [{"old_archive": n} for n in range(20)]
    sheets._save("book", book)
    with pytest.raises(StateConflict, match="capacity"):
        sheets.approve_grid_migration(plan, after, plan.plan_id, "config", "code")
    assert sheets.read("book")["grid_archives"] == book["grid_archives"]


def test_removed_tab_is_explicitly_archived_and_duplicate_titles_fail(migration):
    sheets, before, after, plan = migration
    sheets.approve_grid_migration(plan, after, plan.plan_id, "config", "code")
    sheets.apply("book", "layout", after, "new")
    backwards = sheets.plan_grid_migration("book", before)
    assert ("archive", "report") in backwards.changes
    view = restore_workbook_view(before["view"])
    duplicate = replace(view, tabs=(*view.tabs, replace(view.tabs[0], tab_key="other", title="sCoRe")))
    with pytest.raises(ValueError, match="titles"):
        sheets.plan_grid_migration("book", {**before, "view": asdict(duplicate), "view_id": duplicate.view_id})


@pytest.mark.parametrize("fault", [None, "after"])
def test_confirmed_migration_still_requires_sealed_publication_and_reconciliation(migration, tmp_path, fault):
    sheets, _, after, plan = migration
    with closing(LocalStateStore(tmp_path / "state.sqlite")) as store:
        job = store.submit_job("request", TaskSpec("recompute_healthcheck", "union", "code"), is_desired=True)
        lease = store.claim("worker", "code")
        store.complete_compute(lease, {"workbooks": {"book": after}, "layout_revisions": {"book": "layout"}})
        claim = store.begin_publication(job.job_id, "publisher", "code", ("book",))
        sheets.approve_grid_migration(plan, after, plan.plan_id, "config", "code")
        if fault is None:
            publish_workbooks(store, sheets, claim, {"book": after}, {"book": "layout"})
        else:
            with pytest.raises(TimeoutError):
                publish_workbooks(store, sheets, claim, {"book": after}, {"book": "layout"}, {"book": fault})
            assert store.get_job(job.job_id).state == "publish_uncertain"
            observed = sheets.observe("book", after, claim.commit_id)
            assert store.recover_publication(claim.publication_id, "reconciler", {"book": observed}, True) is None
        assert store.get_job(job.job_id).state == "succeeded"
        assert len(sheets.read("book")["grid_archives"]) == 1
