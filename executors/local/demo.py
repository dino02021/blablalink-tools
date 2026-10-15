"""Synthetic end-to-end exercise; no real provider or credential paths."""

from dataclasses import asdict
import json
from pathlib import Path
import sqlite3

from adapters.local_state import LocalStateStore
from adapters.mock_sheets import MockSheets
from adapters.sheet_standard import StandardReference
from application.publication import publish_workbooks
from application.sheet_healthcheck import reconcile_standard, run_healthcheck_job
from application.tasks import TaskSpec
from domain.normalization import capture_raw_training, normalize_training
from domain.snapshots import MemberKey, canonical_json, capture_document
from domain.mock_workspace import MOCK_WORKSPACE_MARKER as MARKER

def run_demo(directory: Path) -> dict:
    directory = Path(directory).resolve()
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("demo requires a new or empty directory")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "mock-workspace.json").write_text(json.dumps(MARKER), encoding="utf-8")
    sheets = MockSheets(directory / "workbooks")
    sheets.create("standard_demo", "standard_schema_v1", {"standard_rows": standard_rows(5)})
    sheets.create("output_demo", "output_schema_v1", {"manual_notes": {"member_a": "keep"}})
    store = LocalStateStore(directory / "state.sqlite")
    try:
        reference = StandardReference("reference_demo", ("role_demo",), ())
        candidate = reconcile_standard(store, sheets, "standard_demo", "common", "2026-10-15T00:00:00Z", reference)
        # Simulated collaborator edits after candidate capture. Approval below names only the old candidate.
        sheets.edit_inputs("standard_demo", {"standard_rows": standard_rows(9)})
        raw = capture_raw_training("union_demo", "2026-10-15T00:00:00Z", reference.version,
            tuple(raw_member(member_id, level) for member_id, level in (("member_a", 6), ("member_b", 3))))
        normalized = normalize_training(raw, reference.role_ids, {})
        store.put_document(raw)
        training_id = store.put_document(normalized.training)
        roster = tuple(MemberKey("area_demo", member_id) for member_id in ("member_a", "member_b"))
        inputs = capture_document("union_inputs", "union_demo", "2026-10-15T00:00:00Z", reference.version,
            {"members": [{"member": asdict(member), "display_name": "same_mock_name"} for member in roster],
             "healthcheck_route": {"resource": "output_demo", "layout_revision": "output_schema_v1"}})
        inputs_id = store.put_document(inputs)
        task = TaskSpec("recompute_healthcheck", "union_demo", "mock-release-v1", {"training": training_id, "union_inputs": inputs_id})
        publication = store.publish_candidate(candidate, (task,))
        lease = store.claim("mock-worker", "mock-release-v1")
        computed = run_healthcheck_job(store, lease)
        result = store.get_result(computed.result_id)
        claim = store.begin_publication(computed.job_id, "mock-publisher", "mock-release-v1", tuple(result["workbooks"]))
        publish_workbooks(store, sheets, claim, result["workbooks"], result["layout_revisions"])
        pending = reconcile_standard(store, sheets, "standard_demo", "common", "2026-10-15T00:01:00Z", reference)
        return {"is_mock": True, "state": store.get_job(computed.job_id).state,
            "published_config_id": publication.config_id, "output_config_id": sheets.read("output_demo")["generated"]["config_id"],
            "pending_candidate_id": pending, "raw_snapshot_id": raw.snapshot_id,
            "training_snapshot_id": training_id, "job_id": computed.job_id, "network_providers": []}
    finally:
        store.close()


def workspace_status(directory: Path) -> dict:
    directory = Path(directory).resolve()
    marker = directory / "mock-workspace.json"
    if (not marker.is_file() or marker.stat().st_size > 4096 or canonical_json(json.loads(marker.read_text(encoding="utf-8"))) != canonical_json(MARKER)
            or not (directory / "state.sqlite").is_file()):
        raise ValueError("not an initialized mock workspace")
    connection = sqlite3.connect((directory / "state.sqlite").as_uri()+"?mode=ro", uri=True, isolation_level=None, timeout=2)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        return {"is_mock": True, "jobs": [dict(row) for row in connection.execute(
            "SELECT job_id,state,error_code FROM jobs ORDER BY created_at,job_id")]}
    finally:
        connection.close()


def standard_rows(threshold: int) -> list[dict]:
    return [{"name_code": "role_demo", "display_name": "mock_role", "elements": ["電擊"], "role_type": "output",
             "tracking": "追蹤", "total_weight": 1,
             "metrics": [{"metric_id": "skill_1", "threshold": threshold, "coefficient": 1}]}]


def raw_member(member_id: str, skill: int) -> dict:
    return {"area_id": "area_demo", "member_id": member_id,
        "ownership": {"status": "complete", "characters": [{"name_code": "role_demo", "grade": 0, "core": 0}]},
        "details": {"status": "complete", "is_options_complete": True, "requested_codes": ["role_demo"],
                    "character_details": [{"name_code": "role_demo", "skill1_lv": skill}], "state_effects": []}}
