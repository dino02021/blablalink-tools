"""Offline publication orchestration over explicit state and workbook adapters."""

from __future__ import annotations

from application.tasks import PublicationClaim, StateConflict
from domain.snapshots import canonical_json


def publish_workbooks(store, sheets, claim: PublicationClaim, outputs: dict,
                      revisions: dict, faults: dict | None = None) -> None:
    """Use a persisted result manifest, never caller-supplied replacement computation."""
    store.validate_publication_claim(claim, is_finished_allowed=True)
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
        meta = store.publication_safety_metadata(claim.publication_id)
        if meta["has_safety_phase"] and meta["is_send_started"] == 0 and hasattr(sheets, "capture_publication_state"):
            for resource in claim.resources:
                if store.publication_base(claim.publication_id, resource) is None:
                    base = sheets.capture_publication_state(resource,claim.publication_id,claim.commit_id,job.result_id,claim.code_sha)
                    store.record_publication_base(claim,resource,base)
        for resource in claim.resources:
            store.record_resource_observation(claim, resource, sheets.observe(resource, outputs[resource], claim.commit_id))
            store.mark_publication_send_started(claim)
            sheets.apply(resource, revisions[resource], outputs[resource], claim.commit_id,
                         fault=(faults or {}).get(resource))
            observed = sheets.observe(resource, outputs[resource], claim.commit_id)
            if observed != claim.commit_id:
                raise StateConflict("workbook content readback failed")
            store.record_resource_observation(claim, resource, observed)
        observations = {resource: sheets.observe(resource, outputs[resource], claim.commit_id)
                        for resource in claim.resources}
        store.complete_publication(claim, observations)
    except (TimeoutError, StateConflict, OSError, ValueError, KeyError, TypeError):
        # A stale claim cannot change the new owner's state; its failed transition is intentional.
        store.mark_publication_uncertain(claim)
        raise
