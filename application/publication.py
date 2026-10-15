"""Offline publication orchestration over explicit state and workbook adapters."""

from __future__ import annotations

from application.tasks import PublicationClaim, StateConflict
from domain.snapshots import canonical_json


def publish_workbooks(store, sheets, claim: PublicationClaim, outputs: dict,
                      revisions: dict, faults: dict | None = None) -> None:
    """Use a persisted result manifest, never caller-supplied replacement computation."""
    job = store.get_job(claim.job_id)
    result = store.get_result(job.result_id)
    if (canonical_json(result.get("workbooks")) != canonical_json(outputs)
            or canonical_json(result.get("layout_revisions")) != canonical_json(revisions)):
        raise StateConflict("publication differs from sealed result")
    if set(outputs) != set(claim.resources) or set(revisions) != set(claim.resources):
        raise StateConflict("publication resource manifest differs")
    if job.state == "succeeded":
        observations = {resource: sheets.observe(resource, outputs[resource], claim.commit_id)
                        for resource in claim.resources}
        store.complete_publication(claim, observations)
        return
    # Revalidate ownership before each write. This cannot fence an already sent HTTP request.
    try:
        for resource in claim.resources:
            store.record_resource_observation(claim, resource, sheets.observe(resource, outputs[resource], claim.commit_id))
            sheets.apply(resource, revisions[resource], outputs[resource], claim.commit_id,
                         fault=(faults or {}).get(resource))
            observed = sheets.observe(resource, outputs[resource], claim.commit_id)
            if observed != claim.commit_id:
                raise StateConflict("workbook content readback failed")
            store.record_resource_observation(claim, resource, observed)
        observations = {resource: sheets.observe(resource, outputs[resource], claim.commit_id)
                        for resource in claim.resources}
        store.complete_publication(claim, observations)
    except (TimeoutError, StateConflict, OSError):
        # A stale claim cannot change the new owner's state; its failed transition is intentional.
        store.mark_publication_uncertain(claim)
        raise
