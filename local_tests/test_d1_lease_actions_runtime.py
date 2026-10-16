import sys

import pytest

from adapters.local_d1_compute_queue import LocalD1ComputeQueue
from adapters.local_d1_objects import LocalD1Objects
from adapters.local_d1_session import LocalD1Session
from application.tasks import LeaseLost, StateConflict, TaskSpec
from coordination.d1_compute_queue import ClaimIntent
from domain.chunks import encode_value
from local_tests.test_d1_compute_queue_runtime import definition, freeze


@pytest.mark.skipif(sys.platform != 'win32', reason='requires verified Windows SDK process supervision')
def test_actual_heartbeat_and_completion_preserve_receipts_across_expiry_and_restart(tmp_path):
    schema = definition()
    session = LocalD1Session(tmp_path / 'd1', schema, is_create=True)
    now = [1000]
    queue = LocalD1ComputeQueue(session, clock_ms=lambda: now[0])
    objects = LocalD1Objects.with_session(session)
    task = TaskSpec('refresh_raid_history', 'mock_union', 'mock-release', intent_id='actions')
    freeze(objects, task)
    queue.enqueue_frozen('enqueue', task)
    claim_intent = ClaimIntent('claim', 'worker', 'mock-release', lease_ms=2000)
    lease = queue.claim(claim_intent)
    now[0] = 1500
    heartbeat = queue.heartbeat('heartbeat', lease, lease_ms=6000)
    assert heartbeat['expires_at_ms'] == 7500
    now[0] = 3500
    assert queue.claim(claim_intent) == lease
    assert queue.claim_receipt('claim')['expires_at_ms'] == 3000
    assert queue.heartbeat('heartbeat', lease, lease_ms=6000) == heartbeat
    manifest, chunks = encode_value({'mock_history': [], 'is_complete': True})
    objects.begin(manifest)
    objects.put_many(manifest, chunks)
    objects.seal(manifest)
    completed = queue.complete('complete', lease, manifest.object_id, is_publication_required=False)
    assert completed.state == 'succeeded' and completed.result_id == manifest.object_id and completed.owner is None
    now[0] = 10000
    reopened = LocalD1ComputeQueue(LocalD1Session(session.directory, schema), clock_ms=lambda: now[0])
    assert reopened.complete('complete', lease, manifest.object_id, is_publication_required=False) == completed
    with pytest.raises(LeaseLost):
        reopened.heartbeat('heartbeat', lease, lease_ms=6000)
    with pytest.raises(StateConflict):
        reopened.complete('complete', lease, 'd' * 64, is_publication_required=False)
