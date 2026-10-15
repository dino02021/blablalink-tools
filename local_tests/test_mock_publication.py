from dataclasses import replace

import pytest

from adapters.local_state import LocalStateStore
from adapters.mock_sheets import MockSheets
from application.publication import publish_workbooks
from application.tasks import LeaseLost, StateConflict, TaskSpec


@pytest.fixture
def setup(tmp_path):
    store = LocalStateStore(tmp_path / "state.sqlite")
    sheets = MockSheets(tmp_path / "books")
    outputs = {"book_a": {"score": [["member_demo", 100]]}, "book_b": {"damage": [[10]]}}
    revisions = {resource: "layout_v1" for resource in outputs}
    for resource in outputs:
        sheets.create(resource, "layout_v1", {"manual_note": "keep"})
    job = store.submit_job("request_demo", TaskSpec("recompute_healthcheck", "union_demo", "code_demo"), is_desired=True)
    lease = store.claim("worker_demo", "code_demo")
    store.complete_compute(lease, {"workbooks": outputs, "layout_revisions": revisions})
    claim = store.begin_publication(job.job_id, "publisher_demo", "code_demo", tuple(outputs))
    yield store, sheets, claim, outputs, revisions
    store.close()


def test_complete_and_atomic_generated_replacement_preserves_human_input(setup):
    store, sheets, claim, outputs, revisions = setup
    sheets.edit_inputs("book_a", {"manual_note": "collaborator changed"})
    publish_workbooks(store, sheets, claim, outputs, revisions)
    assert store.get_job(claim.job_id).state == "succeeded"
    assert sheets.read("book_a")["inputs"] == {"manual_note": "collaborator changed"}
    assert sheets.read("book_a")["generated"] == outputs["book_a"]
    publish_workbooks(store, sheets, claim, outputs, revisions)
    assert sheets.write_count == 2


@pytest.mark.parametrize("fault", ["before", "after"])
def test_partial_outcome_reconciles_same_commit_across_adapter_restart(setup, fault):
    store, sheets, claim, outputs, revisions = setup
    with pytest.raises(TimeoutError):
        publish_workbooks(store, sheets, claim, outputs, revisions, {"book_b": fault})
    assert store.get_job(claim.job_id).state == "publish_uncertain"
    restarted = MockSheets(sheets.directory)
    observations = {r: restarted.observe(r, outputs[r], claim.commit_id) for r in outputs}
    with pytest.raises(StateConflict):
        store.recover_publication(claim.publication_id, "reconciler", observations, False)
    resumed = store.recover_publication(claim.publication_id, "reconciler", observations, True)
    if fault == "after":
        assert resumed is None
        assert store.get_job(claim.job_id).state == "succeeded"
    else:
        assert resumed.commit_id == claim.commit_id
        with pytest.raises((StateConflict, LeaseLost)):
            publish_workbooks(store, restarted, claim, outputs, revisions)
        publish_workbooks(store, restarted, resumed, outputs, revisions)
        assert restarted.write_count == 1  # already verified book_a is not rewritten
        assert store.get_job(claim.job_id).state == "succeeded"


def test_sealed_result_prevents_payload_or_resource_substitution(setup):
    store, sheets, claim, outputs, revisions = setup
    with pytest.raises(StateConflict):
        publish_workbooks(store, sheets, claim, {"book_a": {"score": 0}}, revisions)
    assert sheets.write_count == 0
    assert store.get_job(claim.job_id).state == "publishing"


def test_python_equal_numeric_types_do_not_replace_sealed_json(setup):
    store, sheets, claim, outputs, revisions = setup
    changed = {"book_a": {"score": [["member_demo", 100.0]]}, "book_b": outputs["book_b"]}
    assert changed == outputs  # Python equality is weaker than the serialized input contract.
    with pytest.raises(StateConflict):
        publish_workbooks(store, sheets, claim, changed, revisions)
    assert sheets.write_count == 0


def test_layout_conflict_cannot_overwrite_generated_data(setup):
    store, sheets, claim, outputs, revisions = setup
    book = sheets.read("book_a")
    book["layout_revision"] = "layout_v2"
    sheets._save("book_a", book)  # deliberate mock collaborator schema mutation
    with pytest.raises(StateConflict, match="layout"):
        publish_workbooks(store, sheets, claim, outputs, revisions)
    assert sheets.read("book_a")["generated"] == {}
    assert store.get_job(claim.job_id).state == "publish_uncertain"


def test_commit_marker_without_matching_content_is_not_success(setup):
    store, sheets, claim, outputs, revisions = setup
    sheets.apply("book_a", "layout_v1", outputs["book_a"], claim.commit_id)
    book = sheets.read("book_a")
    book["generated"] = {"score": [[999]]}
    sheets._save("book_a", book)
    assert sheets.observe("book_a", outputs["book_a"], claim.commit_id) is None
    with pytest.raises(StateConflict, match="content"):
        publish_workbooks(store, sheets, claim, outputs, revisions)
    assert store.get_job(claim.job_id).state == "publish_uncertain"


def test_invalid_or_stale_publisher_cannot_write_any_workbook(setup):
    store, sheets, claim, outputs, revisions = setup
    forged = replace(claim, generation=claim.generation + 1)
    with pytest.raises((StateConflict, LeaseLost)):
        publish_workbooks(store, sheets, forged, outputs, revisions)
    assert sheets.write_count == 0
    assert store.get_job(claim.job_id).state == "publishing"


def test_mock_resource_cannot_escape_directory(setup):
    _, sheets, _, _, _ = setup
    sheets.create("../../outside", "layout_v1", {})
    assert sheets._path("../../outside").parent == sheets.directory


def test_atomic_replacement_clears_previous_generated_rows(setup):
    _, sheets, claim, outputs, _ = setup
    sheets.apply("book_a", "layout_v1", {"score": [[1], [2], [3]]}, "old_commit")
    sheets.apply("book_a", "layout_v1", outputs["book_a"], claim.commit_id)
    assert sheets.read("book_a")["generated"] == outputs["book_a"]


def test_coordinator_restart_retains_partial_publication_and_recovers(setup, tmp_path):
    store, sheets, claim, outputs, revisions = setup
    with pytest.raises(TimeoutError):
        publish_workbooks(store, sheets, claim, outputs, revisions, {"book_b": "before"})
    store.close()
    restarted = LocalStateStore(tmp_path / "state.sqlite")
    try:
        assert restarted.get_job(claim.job_id).state == "publish_uncertain"
        books = MockSheets(sheets.directory)
        observations = {r: books.observe(r, outputs[r], claim.commit_id) for r in outputs}
        resumed = restarted.recover_publication(claim.publication_id, "reconciler", observations, True)
        publish_workbooks(restarted, books, resumed, outputs, revisions)
        assert restarted.get_job(claim.job_id).state == "succeeded"
        assert books.write_count == 1
    finally:
        restarted.close()
