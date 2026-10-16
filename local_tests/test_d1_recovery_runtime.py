import sys

import pytest

from adapters.local_d1_compute_queue import LocalD1ComputeQueue
from adapters.local_d1_objects import LocalD1Objects
from adapters.local_d1_session import LocalD1Session
from application.tasks import LeaseLost, TaskSpec
from coordination.d1_binary import D1Binary
from coordination.d1_checkpoints import SCHEMA_STATEMENTS as CHECKPOINTS
from coordination.d1_compute_queue import ClaimIntent
from coordination.d1_recovery import SCHEMA_STATEMENTS as RECOVERY, RecoveryIntent, build_recovery
from coordination.d1_schema import D1Schema
from coordination.d1_statement import D1Statement
from domain.chunks import encode_value
from local_tests.test_d1_compute_queue_runtime import definition as queue_definition, freeze


@pytest.mark.skipif(sys.platform != 'win32', reason='requires verified Windows SDK process supervision')
def test_actual_recovery_resumes_checkpoint_and_preserves_claim_and_recovery_receipts(tmp_path):
    schema = D1Schema((*queue_definition().statements, *CHECKPOINTS, *RECOVERY))
    session = LocalD1Session(tmp_path / 'd1', schema, is_create=True)
    binary = D1Binary(bytes(range(256)) * 128)
    # Maximum chunk and the full schema guard share the same 64 KiB control frame.
    echoed = session.execute((D1Statement('SELECT ? AS chunk_data', (binary,)),))
    assert D1Binary.from_wire(echoed[0]['results'][0]['chunk_data']) == binary
    now = [1000]
    queue = LocalD1ComputeQueue(session, clock_ms=lambda: now[0])
    objects = LocalD1Objects.with_session(session)
    task = TaskSpec('refresh_raid_history', 'mock_union', 'mock-release', intent_id='recovery', max_attempts=2)
    freeze(objects, task)
    queue.enqueue_frozen('enqueue', task)
    first_intent = ClaimIntent('claim-first', 'worker-first', 'mock-release', lease_ms=1000)
    first = queue.claim(first_intent)
    manifest, chunks = encode_value({'checkpoint_kind': 'mock_cursor', 'offset': 10})
    objects.begin(manifest)
    objects.put_many(manifest, chunks)
    objects.seal(manifest)
    queue.save_checkpoint('checkpoint-first', first, manifest.object_id, base_revision=0)
    now[0] = 2000
    receipt = queue.recover('tick-first', limit=1, scope_id='mock_union')
    assert receipt['items'][0]['target_state'] == 'retry_wait'
    assert queue.get_job(task.fingerprint).error_code == 'lease_expired'
    restarted = LocalD1ComputeQueue(LocalD1Session(session.directory, schema), clock_ms=lambda: now[0])
    second = restarted.claim(ClaimIntent('claim-second', 'worker-second', 'mock-release', lease_ms=1000))
    assert second.generation == 2 and restarted.get_job(task.fingerprint).error_code is None
    progress = restarted.load_checkpoint_version(task.fingerprint)
    assert progress.revision == 1 and progress.generation == 1 and progress.payload['offset'] == 10
    with pytest.raises(LeaseLost):
        restarted.save_checkpoint('old-generation', first, manifest.object_id, base_revision=1)
    assert restarted.save_checkpoint('checkpoint-second', second, manifest.object_id, base_revision=1)['revision'] == 2
    assert restarted.recover('tick-first', limit=1, scope_id='mock_union') == receipt
    assert restarted.get_job(task.fingerprint).state == 'running'
    now[0] = 3000
    terminal = restarted.recover('tick-terminal', limit=1)
    assert terminal['items'][0]['target_state'] == 'failed'
    assert restarted.get_job(task.fingerprint).error_code == 'lease_expired'
    assert restarted.claim(ClaimIntent('nothing', 'worker', 'mock-release')) is None
    idle = session.execute(build_recovery(RecoveryIntent('idle'), now[0]))
    assert idle[-2]['results'] == [] and idle[-1]['results'] == []
    assert sum(result['meta']['rows_written'] for result in idle) == 0
