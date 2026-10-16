import sys

import pytest

from adapters.local_d1_compute_queue import LocalD1ComputeQueue
from adapters.local_d1_objects import LocalD1Objects
from adapters.local_d1_session import LocalD1Session
from application.tasks import ComputeLease, LeaseLost, StateConflict, TaskSpec
from coordination.d1_checkpoints import SCHEMA_STATEMENTS
from coordination.d1_compute_queue import ClaimIntent
from coordination.d1_schema import D1Schema
from coordination.d1_statement import D1Statement
from domain.chunks import encode_value
from local_tests.test_d1_compute_queue_runtime import definition as queue_definition, freeze


@pytest.mark.skipif(sys.platform != 'win32', reason='requires verified Windows SDK process supervision')
def test_actual_checkpoint_cas_replay_and_generation_handoff_preserve_latest_progress(tmp_path):
    schema = D1Schema((*queue_definition().statements, *SCHEMA_STATEMENTS))
    session = LocalD1Session(tmp_path / 'd1', schema, is_create=True)
    queue = LocalD1ComputeQueue(session, clock_ms=lambda: 1000)
    objects = LocalD1Objects.with_session(session)
    task = TaskSpec('refresh_raid_history', 'mock_union', 'mock-release', intent_id='checkpoint')
    freeze(objects, task)
    queue.enqueue_frozen('enqueue', task)
    lease = queue.claim(ClaimIntent('claim', 'worker', 'mock-release'))
    assert queue.load_checkpoint_version(task.fingerprint) is None
    references = []
    for offset in (0, 10):
        manifest, chunks = encode_value({'checkpoint_kind': 'mock_cursor', 'offset': offset})
        objects.begin(manifest)
        objects.put_many(manifest, chunks)
        objects.seal(manifest)
        references.append(manifest.object_id)
    first = queue.save_checkpoint('first', lease, references[0], base_revision=0)
    second = queue.save_checkpoint('second', lease, references[1], base_revision=1)
    assert (first['revision'], second['revision']) == (1, 2)
    restarted = LocalD1ComputeQueue(LocalD1Session(session.directory, schema), clock_ms=lambda: 1000)
    assert restarted.save_checkpoint('first', lease, references[0], base_revision=0) == first
    current = restarted.load_checkpoint_version(task.fingerprint)
    assert current.revision == 2 and current.payload['offset'] == 10
    with pytest.raises(StateConflict):
        restarted.save_checkpoint('stale', lease, references[0], base_revision=1)
    # Explicit handoff metadata fixture; the recovery API is developed separately.
    session.execute((D1Statement("UPDATE compute_jobs SET owner='worker-2',generation=2,attempt=2,expires_at_ms=61000 WHERE job_id=?", (task.fingerprint,)),))
    with pytest.raises(LeaseLost):
        restarted.save_checkpoint('old-worker', lease, references[0], base_revision=2)
    assert restarted.load_checkpoint_version(task.fingerprint).generation == 1
    successor = ComputeLease(task.fingerprint, 'worker-2', 'mock-release', 2)
    assert restarted.save_checkpoint('resume', successor, references[1], base_revision=2)['revision'] == 3
    assert restarted.load_checkpoint_version(task.fingerprint).payload == current.payload
