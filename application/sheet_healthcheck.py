"""Offline sheet draft reconciliation and snapshot-bound calculation manifest."""

from adapters.sheet_standard import StandardReference, read_standard_sheet
from adapters.legacy_standard import LegacyCatalog, read_legacy_standard
from application.healthcheck_artifact import HealthcheckInputsPending, build_healthcheck_artifact
from application.compute_budget import ComputeExecutionCheck, ComputeLimit
from application.tasks import StateConflict
from domain.execution import check_execution, execution_checks
from domain.snapshots import canonical_json


def reconcile_standard(store, sheets, resource: str, scope_id: str, captured_at: str, reference: StandardReference) -> str | None:
    captured = read_standard_sheet(sheets.read(resource), scope_id, captured_at, reference)
    return _reconcile_captured(store, captured, scope_id, reference.version)


def reconcile_legacy_standard(store, sheets, resource: str, scope_id: str, captured_at: str, catalog: LegacyCatalog) -> str | None:
    captured = read_legacy_standard(sheets.read(resource), scope_id, captured_at, catalog)
    return _reconcile_captured(store, captured, scope_id, catalog.reference.version)


def run_healthcheck_job(store, lease, budget=None):
    if budget is None:
        return _run_healthcheck_job(store, lease)
    try:
        with execution_checks(ComputeExecutionCheck(store, lease, budget)):
            check_execution()
            return _run_healthcheck_job(store, lease)
    except ComputeLimit as error:
        return store.defer(lease, "capacity_blocked", error.code)


def run_standard_reconcile_job(store, sheets, lease, captured_at, budget):
    try:
        with execution_checks(ComputeExecutionCheck(store, lease, budget)):
            check_execution()
            job = store.get_job(lease.job_id)
            if job.spec.command != "reconcile" or job.spec.family != "config_reconcile":
                raise StateConflict("not a standard reconciliation job")
            route = store.get_document(job.spec.version_map["reconcile_route"])
            if (route.envelope["kind"] != "union_inputs" or route.envelope["scope_id"] != job.spec.scope_id
                    or route.payload.get("reconcile_kind") != "structured_healthcheck_standard"):
                raise StateConflict("reconciliation route differs")
            payload = route.payload
            reference = StandardReference(route.envelope["reference_version"], tuple(payload["role_ids"]), tuple(payload["function_ids"]))
            candidate_id = reconcile_standard(store, sheets, payload["resource"], job.spec.scope_id, captured_at, reference)
            check_execution()
            return store.complete_compute(lease, {"candidate_id": candidate_id, "route_snapshot_id": route.snapshot_id}, is_publication_required=False)
    except ComputeLimit as error:
        return store.defer(lease, "capacity_blocked", error.code)


def _run_healthcheck_job(store, lease):
    job = store.get_job(lease.job_id)
    if job.spec.command != "recompute_healthcheck":
        raise StateConflict("not a healthcheck task")
    versions = job.spec.version_map
    config = store.get_document(versions["healthcheck_config"])
    training = store.get_document(versions["training"])
    inputs = store.get_document(versions["union_inputs"])
    try:
        artifact = build_healthcheck_artifact(config, training, inputs, job.spec.scope_id, job.spec.code_sha)
    except HealthcheckInputsPending as error:
        return store.defer(lease, "waiting_for_data", error.code)
    return store.complete_compute(lease, artifact)


def _reconcile_captured(store, captured, scope_id, reference_version):
    head = store.config_head("healthcheck", scope_id)
    if head:
        published = store.get_document(head)
        if canonical_json(published.payload) == canonical_json(captured.payload) and published.envelope["reference_version"] == reference_version:
            return None
    # Reconciliation prepares a fixed candidate; it never confirms or publishes it.
    return store.create_candidate(captured, head)
