import sqlite3

import pytest

from application.tasks import ComputeLease
from coordination.d1_compute_queue import ClaimIntent, claim
from coordination.d1_lease_actions import ComputeActionIntent, active_lease, build_action, verify_action
from local_tests.test_d1_compute_queue import batch, database, enqueue, spec


def running(connection):
    task = spec()
    enqueue(connection, task)
    receipt = batch(connection, claim(ClaimIntent('claim', 'worker', 'mock-release', lease_ms=1000), 1000))[-1][0]
    return ComputeLease(task.fingerprint, 'worker', 'mock-release', receipt['generation'])


def sealed_result_metadata(connection, identity='c' * 64):
    connection.execute('INSERT INTO object_transports VALUES(?,?,?,1)', (identity, identity, 'metadata-fixture'))
    connection.execute('INSERT INTO object_heads VALUES(?,?)', (identity, identity))
    connection.commit()
    return identity


def test_heartbeat_replay_does_not_extend_expiry_again_and_old_claim_remains_a_valid_identity():
    from coordination.d1_compute_queue import active_claim
    with database() as connection:
        lease = running(connection)
        claim_intent = ClaimIntent('claim', 'worker', 'mock-release', lease_ms=1000)
        original = batch(connection, claim(claim_intent, 1000))[-1][0]
        intent = ComputeActionIntent('heartbeat', lease, 'heartbeat', lease_ms=5000)
        receipt = batch(connection, build_action(intent, 1500))[-1]
        assert verify_action(intent, receipt)['expires_at_ms'] == 6500
        assert batch(connection, build_action(intent, 3000))[-1] == receipt
        assert batch(connection, (active_claim(claim_intent, original, 3000),))[0] == [{'job_id': lease.job_id}]
        assert batch(connection, (active_lease(lease, 6500),))[0] == []


@pytest.mark.parametrize('is_publication_required', [False, True])
def test_sealed_result_completion_is_atomic_immutable_and_replayable_after_lease_clears(is_publication_required):
    with database() as connection:
        lease = running(connection)
        result_id = sealed_result_metadata(connection)
        intent = ComputeActionIntent('complete', lease, 'complete', result_id=result_id, is_publication_required=is_publication_required)
        receipt = batch(connection, build_action(intent, 1500))[-1]
        assert verify_action(intent, receipt)['outcome_state'] == ('computed' if is_publication_required else 'succeeded')
        assert batch(connection, build_action(intent, 5000))[-1] == receipt
        row = connection.execute('SELECT owner,expires_at_ms,result_id FROM compute_jobs').fetchone()
        assert tuple(row) == (None, None, result_id)
        different = ComputeActionIntent('complete', lease, 'complete', result_id='d' * 64, is_publication_required=is_publication_required)
        with pytest.raises(sqlite3.DatabaseError):
            batch(connection, build_action(different, 5000))


@pytest.mark.parametrize('mode', ['unsealed', 'expired', 'wrong_owner', 'wrong_generation'])
def test_invalid_completion_never_changes_job_or_records_action(mode):
    with database() as connection:
        lease = running(connection)
        result_id = sealed_result_metadata(connection) if mode != 'unsealed' else 'c' * 64
        now = 1500
        if mode == 'expired': now = 2000
        if mode == 'wrong_owner': lease = ComputeLease(lease.job_id, 'other', lease.code_sha, lease.generation)
        if mode == 'wrong_generation': lease = ComputeLease(lease.job_id, lease.worker_id, lease.code_sha, 2)
        intent = ComputeActionIntent('complete', lease, 'complete', result_id=result_id)
        with pytest.raises(sqlite3.DatabaseError):
            batch(connection, build_action(intent, now))
        assert connection.execute('SELECT COUNT(*) FROM compute_actions').fetchone()[0] == 0
        assert connection.execute('SELECT state,result_id FROM compute_jobs').fetchone()[:] == ('running', None)


def test_source_changes_quarantine_the_sealed_result_as_superseded():
    from coordination.d1_compute_queue import SourcePin
    with database() as connection:
        task = spec(versions={'raw': 'b' * 64})
        connection.execute('INSERT INTO contract_heads VALUES(?,?,?,?,?)', ('mock_union', 'raw', 'b' * 64, 'mock-release', 1))
        connection.commit()
        enqueue(connection, task, pins=(SourcePin('raw','mock_union','raw','b'*64,1,'mock-release'),))
        batch(connection, claim(ClaimIntent('claim','worker','mock-release'),1000))
        lease = ComputeLease(task.fingerprint,'worker','mock-release',1)
        result_id = sealed_result_metadata(connection)
        connection.execute('UPDATE contract_heads SET revision=2')
        connection.commit()
        intent = ComputeActionIntent('complete',lease,'complete',result_id=result_id)
        assert verify_action(intent,batch(connection,build_action(intent,1500))[-1])['outcome_state']=='superseded'


@pytest.mark.parametrize('action', ['heartbeat', 'complete'])
def test_silently_ignored_job_update_does_not_get_a_success_receipt(action):
    with database() as connection:
        lease = running(connection)
        result = sealed_result_metadata(connection)
        connection.execute('CREATE TRIGGER ignored BEFORE UPDATE ON compute_jobs BEGIN SELECT RAISE(IGNORE); END')
        connection.commit()
        intent = (ComputeActionIntent('action', lease, 'heartbeat', lease_ms=5000) if action == 'heartbeat'
                  else ComputeActionIntent('action', lease, 'complete', result_id=result))
        # Bypass the global schema guard to exercise the write postcondition itself.
        with pytest.raises(sqlite3.DatabaseError):
            batch(connection, build_action(intent, 1500))
        assert connection.execute('SELECT COUNT(*) FROM compute_actions').fetchone()[0] == 0
