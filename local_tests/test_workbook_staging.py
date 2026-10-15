from dataclasses import replace
import hashlib

import pytest

from adapters.local_state import LocalStateStore
from adapters.local_workbook_staging import LocalWorkbookStaging
from adapters.mock_sheets import MockSheets
from application.publication import publish_workbooks
from application.sheet_healthcheck import run_healthcheck_job
from application.tasks import StateConflict
from application.workbook_staging import StagedSheets, WorkbookStage, prepare_workbook_stage
from domain.chunks import encode_document
from domain.snapshots import capture_document
from executors.local.demo import run_demo


@pytest.fixture
def staged(tmp_path):
    directory = tmp_path / "mock"
    summary = run_demo(directory)
    store = LocalStateStore(directory / "state.sqlite")
    sheets = MockSheets(directory / "workbooks")
    staging = LocalWorkbookStaging(directory / "staging.sqlite")
    config = store.candidate_document(summary["pending_candidate_id"])
    old = store.get_job(summary["job_id"])
    target = replace(old.spec, versions={**old.spec.version_map, "healthcheck_config": config.snapshot_id})
    store.publish_candidate(summary["pending_candidate_id"], (target,))
    lease = store.claim("worker", "mock-release-v1")
    job = run_healthcheck_job(store, lease)
    claim = store.begin_publication(job.job_id, "publisher", "mock-release-v1", ("output_demo",))
    stage, chunks = prepare_workbook_stage(store, sheets, claim, "output_demo")
    staging.begin_stage(stage)
    yield store, sheets, staging, claim, stage, chunks, directory
    staging.close()
    store.close()


def test_incomplete_and_cancelled_stage_preserves_result_and_human_data(staged):
    _, sheets, staging, _, stage, _, _ = staged
    before = sheets.read(stage.resource)
    with pytest.raises(StateConflict):
        staging.seal_stage(stage.stage_id)
    with pytest.raises(StateConflict):
        staging.activate(stage.stage_id, sheets)
    staging.cancel_stage(stage.stage_id)
    assert staging.state(stage.stage_id) == "cancelled"
    assert sheets.read(stage.resource) == before


def test_full_stage_publishes_sealed_result_and_keeps_new_manual_input(staged):
    store, sheets, staging, claim, stage, chunks, _ = staged
    upload(staging, stage, chunks)
    sheets.edit_inputs(stage.resource, {"manual_notes": {"member_a": "edited while staging"}})
    result = store.get_result(stage.result_id)
    wrapper = StagedSheets(store, sheets, staging, {stage.resource: stage.stage_id})
    publish_workbooks(store, wrapper, claim, result["workbooks"], result["layout_revisions"])
    assert staging.state(stage.stage_id) == "activated"
    assert store.get_job(stage.job_id).state == "succeeded"
    book = sheets.read(stage.resource)
    assert book["inputs"]["manual_notes"]["member_a"] == "edited while staging"
    assert book["generated"] == result["workbooks"][stage.resource]


def test_layout_change_blocks_activation(staged):
    _, sheets, staging, _, stage, chunks, _ = staged
    upload(staging, stage, chunks)
    book = sheets.read(stage.resource)
    before = book["generated"]
    book["layout_revision"] = "changed_schema"
    sheets._save(stage.resource, book)
    with pytest.raises(StateConflict):
        staging.activate(stage.stage_id, sheets)
    assert sheets.read(stage.resource)["generated"] == before
    assert staging.state(stage.stage_id) == "sealed"


@pytest.mark.parametrize("fault", ["before", "after"])
def test_unknown_stage_never_retries_without_verified_reconciliation(staged, fault):
    store, sheets, staging, claim, stage, chunks, _ = staged
    upload(staging, stage, chunks)
    result = store.get_result(stage.result_id)
    wrapper = StagedSheets(store, sheets, staging, {stage.resource: stage.stage_id})
    with pytest.raises(TimeoutError):
        publish_workbooks(store, wrapper, claim, result["workbooks"], result["layout_revisions"], {stage.resource: fault})
    assert store.get_job(stage.job_id).state == "publish_uncertain"
    assert staging.state(stage.stage_id) == "activation_uncertain"
    with pytest.raises(StateConflict):
        staging.activate(stage.stage_id, sheets)
    with pytest.raises(StateConflict):
        staging.cancel_stage(stage.stage_id)
    with pytest.raises(StateConflict):
        staging.reconcile_stage(stage.stage_id, sheets, False)
    if fault == "after":
        staging.reconcile_stage(stage.stage_id, sheets, True)
        assert staging.state(stage.stage_id) == "activated"
        assert store.get_job(stage.job_id).state == "publish_uncertain"  # stage verification grants no lock release
    else:
        with pytest.raises(StateConflict):
            staging.reconcile_stage(stage.stage_id, sheets, True)


def test_partial_chunk_upload_resumes_after_restart(tmp_path):
    generated = {"rows": [hashlib.sha256(str(index).encode()).hexdigest() for index in range(4000)]}
    document = capture_document("union_inputs", "resource", "1970-01-01T00:00:00Z", "code",
        {"staging_kind": "workbook_generated", "generated": generated})
    manifest, chunks = encode_document(document)
    assert len(chunks) > 1
    stage = WorkbookStage("resource", "schema", "job", "result", "commit", None, manifest)
    path = tmp_path / "stages.sqlite"
    staging = LocalWorkbookStaging(path)
    staging.begin_stage(stage)
    staging.put_stage_chunk(stage.stage_id, 0, chunks[0])
    staging.close()
    restarted = LocalWorkbookStaging(path)
    try:
        assert restarted.missing(manifest.object_id) == tuple(range(1, len(chunks)))
        upload(restarted, stage, chunks)
        assert restarted.payload(stage.stage_id) == generated
        restarted.cancel_stage(stage.stage_id)
        with pytest.raises(StateConflict):
            restarted.put_stage_chunk(stage.stage_id, 0, chunks[0])
    finally:
        restarted.close()


def upload(staging, stage, chunks):
    for index, chunk in enumerate(chunks):
        staging.put_stage_chunk(stage.stage_id, index, chunk)
    staging.seal_stage(stage.stage_id)
