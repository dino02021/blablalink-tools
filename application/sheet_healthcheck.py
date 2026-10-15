"""Offline sheet draft reconciliation and snapshot-bound calculation manifest."""

from dataclasses import asdict

from adapters.sheet_standard import StandardReference, read_standard_sheet
from application.healthcheck import IncompleteTraining, calculate_healthcheck
from application.tasks import StateConflict
from domain.snapshots import MemberKey, Override, canonical_json, read_config


def reconcile_standard(store, sheets, resource: str, scope_id: str, captured_at: str, reference: StandardReference) -> str | None:
    captured = read_standard_sheet(sheets.read(resource), scope_id, captured_at, reference)
    head = store.config_head("healthcheck", scope_id)
    if head:
        published = store.get_document(head)
        if canonical_json(published.payload) == canonical_json(captured.payload) and published.envelope["reference_version"] == reference.version:
            return None
    # Reconciliation prepares a fixed candidate; it never confirms or publishes it.
    return store.create_candidate(captured, head)


def run_healthcheck_job(store, lease):
    job = store.get_job(lease.job_id)
    if job.spec.command != "recompute_healthcheck":
        raise StateConflict("not a healthcheck task")
    versions = job.spec.version_map
    config = store.get_document(versions["healthcheck_config"])
    training = store.get_document(versions["training"])
    inputs = store.get_document(versions["union_inputs"])
    envelope = inputs.envelope
    if (envelope["kind"] != "union_inputs" or envelope["scope_id"] != job.spec.scope_id
            or training.envelope["scope_id"] != job.spec.scope_id
            or envelope["reference_version"] != training.envelope["reference_version"]):
        raise StateConflict("healthcheck routing or roster input scope differs")
    roster = tuple(MemberKey(**row["member"]) for row in inputs.payload["members"])
    display_names = {MemberKey(**row["member"]).key: row["display_name"] for row in inputs.payload["members"]}
    overrides = tuple(Override(**{**row, "member": MemberKey(**row["member"])}) for row in inputs.payload.get("overrides", []))
    route = inputs.payload["healthcheck_route"]
    if set(route) != {"resource", "layout_revision"} or any(not isinstance(v, str) or not v.strip() for v in route.values()):
        raise ValueError("fixed workbook route and layout required")
    try:
        result = calculate_healthcheck(config, training, roster, display_names, job.spec.code_sha, overrides)
    except IncompleteTraining:
        return store.defer(lease, "waiting_for_data", "training_incomplete")
    # The standards rendered for upload are the very configuration used for calculation.
    read_config(config)
    generated = {"schema_version": 1, "config_id": result.config_id, "snapshot_id": result.snapshot_id,
        "code_sha": result.code_version, "calculation_id": result.result_id, "standards": config.payload,
        "scores": asdict(result.scores), "reports": [asdict(report) for report in result.reports]}
    return store.complete_compute(lease, {"workbooks": {route["resource"]: generated},
        "layout_revisions": {route["resource"]: route["layout_revision"]}})
