from dataclasses import asdict
import json
import sys

import pytest

from adapters.local_d1_compute_queue import LocalD1ComputeQueue
from adapters.local_d1_objects import LocalD1Objects
from adapters.local_d1_session import LocalD1Session
from application.tasks import LeaseLost, StateConflict, TaskSpec
from coordination.d1_compute_queue import SCHEMA_STATEMENTS, ClaimIntent, claim
from coordination.d1_objects import SCHEMA_STATEMENTS as OBJECTS
from coordination.receipt_d1_contract import SCHEMA_STATEMENTS as HEADS
from coordination.d1_schema import D1Schema
from coordination.d1_statement import D1Statement
from domain.chunks import encode_value
from executors.local.emulator_process import run_d1_emulator


pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='requires verified Windows SDK process supervision')


def definition():
    return D1Schema((*OBJECTS, *HEADS, *SCHEMA_STATEMENTS))


def freeze(objects, spec):
    manifest, chunks = encode_value(asdict(spec))
    assert manifest.object_id == spec.request_signature
    objects.begin(manifest)
    objects.put_many(manifest, chunks)
    objects.seal(manifest)


def test_actual_frozen_enqueue_and_lost_claim_response_replay_keep_one_lease(tmp_path):
    schema = definition()
    session = LocalD1Session(tmp_path / 'd1', schema, is_create=True)
    idle = session.execute(claim(ClaimIntent('idle', 'worker', 'mock-release'), 1000))
    assert idle[-1]['results'] == [] and sum(result['meta']['rows_written'] for result in idle) == 0
    now = [1000]
    queue = LocalD1ComputeQueue(session, clock_ms=lambda: now[0])
    objects = LocalD1Objects.with_session(session)
    first = TaskSpec('refresh_raid_history', 'mock_union', 'mock-release', intent_id='first', priority=10)
    second = TaskSpec('refresh_raid_history', 'mock_union', 'mock-release', intent_id='second')
    for spec in (first, second):
        freeze(objects, spec)
        assert queue.enqueue_frozen(spec.intent_id, spec).state == 'queued'
    intent = ClaimIntent('claim', 'worker', 'mock-release', lease_ms=1000)
    # Commit the actual claim, deliberately ignore the returned lease response.
    session.execute(claim(intent, now[0]))
    restarted = LocalD1ComputeQueue(LocalD1Session(session.directory, schema), clock_ms=lambda: now[0])
    lease = restarted.claim(intent)
    assert lease.job_id == first.fingerprint and lease.generation == 1
    assert restarted.get_job(first.fingerprint).attempt == 1
    assert restarted.get_job(second.fingerprint).state == 'queued'
    assert restarted.enqueue_frozen('first', first).state == 'running'
    now[0] = 2000
    with pytest.raises(LeaseLost):
        restarted.claim(intent)
    assert restarted.claim_receipt('claim')['expires_at_ms'] == 2000
    with pytest.raises(StateConflict):
        restarted.claim(ClaimIntent('claim', 'different-worker', 'mock-release', lease_ms=1000))


@pytest.mark.parametrize('same_request', [False, True])
def test_actual_parallel_d1_claims_do_not_duplicate_a_job_generation(tmp_path, same_request):
    schema = definition()
    session = LocalD1Session(tmp_path / 'd1', schema, is_create=True)
    queue = LocalD1ComputeQueue(session, clock_ms=lambda: 1000)
    task = TaskSpec('refresh_raid_history', 'mock_union', 'mock-release', intent_id='parallel')
    freeze(LocalD1Objects.with_session(session), task)
    queue.enqueue_frozen('enqueue', task)
    first = ClaimIntent('claim-first', 'worker', 'mock-release')
    second = first if same_request else ClaimIntent('claim-second', 'worker-2', 'mock-release')
    payload = {'schema_statements': [], 'operations': [
        {'kind': 'parallel_batches', 'batches': [[schema.guard().wire, *[statement.wire for statement in claim(intent, 1000)]] for intent in (first, second)]},
        {'kind': 'query', 'sql': 'SELECT generation,attempt,(SELECT COUNT(*) FROM compute_claims) AS receipts FROM compute_jobs', 'parameters': []},
    ]}
    receipt = run_d1_emulator(payload, mock_directory=session.directory, schema_id=schema.schema_id)
    observed = json.loads(receipt['stdout'])
    assert receipt['outcome'] == 'completed' and observed['are_sdk_children_closed']
    batches = observed['operations'][0]['batches']
    assert all(batch['outcome'] == 'completed' for batch in batches)
    assert sum(bool(batch['results'][-1]['results']) for batch in batches) == (2 if same_request else 1)
    assert observed['operations'][1]['result']['results'] == [{'generation': 1, 'attempt': 1, 'receipts': 1}]
