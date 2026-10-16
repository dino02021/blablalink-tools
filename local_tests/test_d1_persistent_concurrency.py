from concurrent.futures import ThreadPoolExecutor
import sys
import threading

import pytest

from adapters.local_d1_compute_queue import LocalD1ComputeQueue
from adapters.local_d1_objects import LocalD1Objects
from adapters.local_d1_session import LocalD1Session
from adapters.windows_child_job import WindowsChildJob
from application.tasks import StateConflict, TaskSpec
from coordination.d1_compute_queue import ClaimIntent
from coordination.d1_statement import D1Statement
from local_tests.test_d1_compute_queue_runtime import definition, freeze


@pytest.mark.skipif(sys.platform != 'win32', reason='requires verified Windows SDK process supervision')
def test_two_owned_sdk_instances_on_one_persist_directory_keep_one_claim(tmp_path, monkeypatch):
    schema = definition()
    directory = tmp_path / 'd1'
    session = LocalD1Session(directory, schema, is_create=True)
    task = TaskSpec('refresh_raid_history', 'mock_union', 'mock-release', intent_id='two-clients')
    freeze(LocalD1Objects.with_session(session), task)
    LocalD1ComputeQueue(session, clock_ms=lambda: 1000).enqueue_frozen('enqueue', task)
    queues = [LocalD1ComputeQueue(LocalD1Session(directory, schema), clock_ms=lambda: 1000) for _ in range(2)]
    intents = [ClaimIntent('claim-'+str(index), 'worker-'+str(index), 'mock-release') for index in range(2)]
    barrier = threading.Barrier(2)
    attached = threading.Barrier(2)
    participant = threading.local()
    pid_lock = threading.Lock()
    root_pids = set()
    original_attach = WindowsChildJob.attach
    def attach(fence, process):
        original_attach(fence, process)
        if getattr(participant, 'is_first', False):
            participant.is_first = False
            with pid_lock:
                root_pids.add(process.pid)
            attached.wait(timeout=10)
            assert process.poll() is None
    monkeypatch.setattr(WindowsChildJob, 'attach', attach)
    def compete(index):
        barrier.wait(timeout=10)
        participant.is_first = True
        try:
            return queues[index].claim(intents[index])
        except StateConflict:
            # Unknown/contended response retries keep the original request ID.
            return 'unproven'
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(compete, range(2)))
    resolved = [queues[index].claim(intents[index]) if result == 'unproven' else result
                for index, result in enumerate(outcomes)]
    leases = [result for result in resolved if result is not None]
    assert len(root_pids) == 2
    assert len(leases) == 1 and leases[0].job_id == task.fingerprint and leases[0].generation == 1
    proof = session.execute((D1Statement('SELECT generation,attempt,(SELECT COUNT(*) FROM compute_claims) AS receipts FROM compute_jobs', ()),))
    assert proof[0]['results'] == [{'generation': 1, 'attempt': 1, 'receipts': 1}]
    assert queues[0].get_job(task.fingerprint).spec == task
