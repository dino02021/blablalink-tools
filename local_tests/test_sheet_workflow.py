from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from adapters.local_state import LocalStateStore
from adapters.mock_sheets import MockSheets
from adapters.sheet_standard import StandardReference, read_standard_sheet
from application.publication import publish_workbooks
from application.sheet_healthcheck import reconcile_standard, run_healthcheck_job
from domain.snapshots import capture_training
from executors.local.demo import run_demo, standard_rows, workspace_status


def test_candidate_is_frozen_while_new_sheet_edit_remains_unpublished(tmp_path):
    summary = run_demo(tmp_path / "demo")
    sheets = MockSheets(tmp_path / "demo/workbooks")
    output = sheets.read("output_demo")
    generated = output["generated"]
    assert summary["state"] == "succeeded"
    assert generated["config_id"] == summary["published_config_id"] == summary["output_config_id"]
    assert generated["standards"]["rules"][0]["metrics"][0]["threshold"] == 5
    assert sheets.read("standard_demo")["inputs"]["standard_rows"][0]["metrics"][0]["threshold"] == 9
    assert [r["assessments"][0]["metrics"][0]["status"] for r in generated["reports"]] == ["passed", "failed"]
    assert output["inputs"]["manual_notes"] == {"member_a": "keep"}
    assert len({row["member"]["member_id"] for row in generated["reports"]}) == 2
    assert summary["network_providers"] == []
    assert summary["pending_candidate_id"]


def test_reconcile_reuses_semantic_candidate_and_new_threshold_reuses_training(tmp_path):
    directory = tmp_path / "demo"
    summary = run_demo(directory)
    sheets = MockSheets(directory / "workbooks")
    store = LocalStateStore(directory / "state.sqlite")
    reference = StandardReference("reference_demo", ("role_demo",), ())
    try:
        pending = reconcile_standard(store, sheets, "standard_demo", "common", "2026-10-15T00:02:00Z", reference)
        assert pending == summary["pending_candidate_id"]
        first_capture = store.candidate_document(pending)
        assert first_capture.envelope["captured_at"] == "2026-10-15T00:01:00Z"
        old = store.get_job(summary["job_id"])
        target = replace(old.spec, versions={**old.spec.version_map, "healthcheck_config": first_capture.snapshot_id})
        publication = store.publish_candidate(pending, (target,))
        lease = store.claim("new_mock_worker", "mock-release-v1")
        computed = run_healthcheck_job(store, lease)
        result = store.get_result(computed.result_id)
        claim = store.begin_publication(computed.job_id, "new_mock_publisher", "mock-release-v1", ("output_demo",))
        publish_workbooks(store, sheets, claim, result["workbooks"], result["layout_revisions"])
        generated = sheets.read("output_demo")["generated"]
        assert generated["standards"]["rules"][0]["metrics"][0]["threshold"] == 9
        assert generated["snapshot_id"] == summary["training_snapshot_id"]
        assert generated["config_id"] == publication.config_id
        assert all(r["assessments"][0]["metrics"][0]["status"] == "failed" for r in generated["reports"])
        assert reconcile_standard(store, sheets, "standard_demo", "common", "2026-10-15T00:03:00Z", reference) is None
    finally:
        store.close()


def test_incomplete_new_training_preserves_last_workbook_result(tmp_path):
    directory = tmp_path / "demo"
    summary = run_demo(directory)
    sheets = MockSheets(directory / "workbooks")
    before = sheets.read("output_demo")
    store = LocalStateStore(directory / "state.sqlite")
    try:
        old = store.get_job(summary["job_id"])
        incomplete = capture_training("union_demo", "2026-10-15T00:01:00Z", "reference_demo", ())
        store.put_document(incomplete)
        versions = {**old.spec.version_map, "training": incomplete.snapshot_id}
        job = store.submit_job("missing_data_request", replace(old.spec, versions=versions), is_desired=True)
        lease = store.claim("mock_worker", "mock-release-v1")
        result = run_healthcheck_job(store, lease)
        assert result.job_id == job.job_id and result.state == "waiting_for_data" and result.result_id is None
        assert sheets.read("output_demo") == before
    finally:
        store.close()


@pytest.mark.parametrize("change", ["unknown_role", "unknown_column", "unknown_function", "duplicate_role", "empty"])
def test_invalid_sheet_standards_are_not_candidates(change):
    rows = standard_rows(5)
    if change == "unknown_role":
        rows[0]["name_code"] = "unknown"
    elif change == "unknown_column":
        rows[0]["threshhold_typo"] = 5
    elif change == "unknown_function":
        rows[0]["metrics"] = [{"metric_id": "trait_1", "function_id": "unknown_function", "threshold": 5}]
    elif change == "duplicate_role":
        rows *= 2
    else:
        rows = []
    with pytest.raises(ValueError):
        read_standard_sheet({"inputs": {"standard_rows": rows}}, "common", "2026-10-15T00:00:00Z", StandardReference("ref", ("role_demo",), ()))


def test_mock_workspace_cannot_overwrite_existing_files_or_inspect_unmarked_directory(tmp_path):
    (tmp_path / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError):
        run_demo(tmp_path)
    with pytest.raises(ValueError):
        workspace_status(tmp_path)
    assert (tmp_path / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_actual_cli_uses_only_explicit_mock_workspace(tmp_path):
    root = Path(__file__).resolve().parents[1]
    directory = tmp_path / "demo"
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP") if key in os.environ}
    env["PYTHONIOENCODING"] = "utf-8"
    demo = subprocess.run([sys.executable, "-m", "executors.local", "demo", "--workspace", str(directory)],
        cwd=root, env=env, text=True, capture_output=True, check=True, timeout=10)
    assert json.loads(demo.stdout)["state"] == "succeeded"
    status = subprocess.run([sys.executable, "-m", "executors.local", "status", "--workspace", str(directory)],
        cwd=root, env=env, text=True, capture_output=True, check=True, timeout=10)
    assert json.loads(status.stdout)["jobs"][0]["state"] == "succeeded"


def test_presemantic_candidate_is_reused_after_local_schema_upgrade(tmp_path):
    store = LocalStateStore(tmp_path / "state.sqlite")
    reference = StandardReference("reference_demo", ("role_demo",), ())
    book = {"inputs": {"standard_rows": standard_rows(5)}}
    try:
        first = read_standard_sheet(book, "common", "2026-10-15T00:00:00Z", reference)
        candidate = store.create_candidate(first, None)
        store._connection.execute("UPDATE config_candidates SET semantic_id=NULL")
        later = read_standard_sheet(book, "common", "2026-10-15T00:01:00Z", reference)
        assert store.create_candidate(later, None) == candidate
        assert store.candidate_document(candidate) == first
    finally:
        store.close()
