from dataclasses import replace
import sqlite3

import pytest

from coordination.d1_compute_queue import ClaimIntent, SourcePin, active_claim, claim
from coordination.d1_recovery import SCHEMA_STATEMENTS, RecoveryIntent, build_recovery, verify_recovery
from local_tests.test_d1_compute_queue import batch, database, enqueue, spec


def recovery_database():
    connection = database()
    for statement in SCHEMA_STATEMENTS:
        connection.execute(statement)
    connection.commit()
    return connection


def recover(connection, intent, now):
    result = batch(connection, build_recovery(intent, now))
    return verify_recovery(intent, result[-2], result[-1])


def test_expired_lease_recovery_is_bounded_replayable_and_fences_old_generation():
    with recovery_database() as connection:
        task = spec()
        enqueue(connection, task)
        old_intent = ClaimIntent('old', 'worker-old', 'mock-release', lease_ms=1000)
        old = batch(connection, claim(old_intent, 1000))[-1][0]
        assert recover(connection, RecoveryIntent('early'), 1999) == {'receipt': None, 'items': []}
        recovery = RecoveryIntent('recover')
        receipt = recover(connection, recovery, 2000)
        assert receipt['items'][0]['target_state'] == 'retry_wait'
        new = batch(connection, claim(ClaimIntent('new', 'worker-new', 'mock-release'), 2000))[-1][0]
        assert new['generation'] == 2
        assert recover(connection, recovery, 99999) == receipt
        assert batch(connection, (active_claim(old_intent, old, 2000),))[0] == []
        assert connection.execute('SELECT state,generation FROM compute_jobs').fetchone()[:] == ('running', 2)
        with pytest.raises(sqlite3.DatabaseError):
            recover(connection, replace(recovery, limit=1), 99999)


def test_attempt_limit_and_dependency_failure_propagate_in_bounded_ticks():
    with recovery_database() as connection:
        parent = spec('parent', max_attempts=1)
        child = spec('child', dependencies=(parent.fingerprint,))
        grandchild = spec('grandchild', dependencies=(child.fingerprint,))
        for task in (parent, child, grandchild): enqueue(connection, task, task.intent_id)
        batch(connection, claim(ClaimIntent('claim', 'worker', 'mock-release', lease_ms=1000), 1000))
        first = recover(connection, RecoveryIntent('tick-1'), 2000)
        assert [item['job_id'] for item in first['items']] == [parent.fingerprint]
        assert first['items'][0]['target_state'] == 'failed'
        second = recover(connection, RecoveryIntent('tick-2'), 2000)
        assert [item['job_id'] for item in second['items']] == [child.fingerprint]
        assert second['items'][0]['reason'] == 'dependency_failed'
        third = recover(connection, RecoveryIntent('tick-3'), 2000)
        assert [item['job_id'] for item in third['items']] == [grandchild.fingerprint]


def test_scope_and_count_bound_do_not_recover_hosted_running_jobs():
    with recovery_database() as connection:
        for index in range(3):
            task = spec(str(index))
            enqueue(connection, task, str(index))
            batch(connection, claim(ClaimIntent('claim-'+str(index), 'worker', 'mock-release', lease_ms=1000), 1000))
        connection.execute("UPDATE compute_jobs SET executor_type='actions' WHERE job_id=(SELECT job_id FROM compute_jobs ORDER BY job_id LIMIT 1)")
        connection.commit()
        assert recover(connection, RecoveryIntent('wrong-scope', scope_id='other'), 2000)['receipt'] is None
        assert len(recover(connection, RecoveryIntent('first', limit=1), 2000)['items']) == 1
        assert len(recover(connection, RecoveryIntent('second', limit=1), 2000)['items']) == 1
        assert recover(connection, RecoveryIntent('third'), 2000)['receipt'] is None
        assert connection.execute("SELECT COUNT(*) FROM compute_jobs WHERE state='running' AND executor_type='actions'").fetchone()[0] == 1


def test_stale_queued_input_is_quarantined_and_empty_tick_has_no_business_write():
    with recovery_database() as connection:
        before = connection.total_changes
        assert recover(connection, RecoveryIntent('idle'), 1000)['receipt'] is None
        assert connection.total_changes == before
        task = spec(versions={'raw':'b'*64})
        connection.execute('INSERT INTO contract_heads VALUES(?,?,?,?,?)', ('mock_union','raw','b'*64,'mock-release',1))
        connection.commit()
        enqueue(connection,task,pins=(SourcePin('raw','mock_union','raw','b'*64,1,'mock-release'),))
        connection.execute('UPDATE contract_heads SET revision=2')
        connection.commit()
        result = recover(connection,RecoveryIntent('idle'),1000)
        assert result['items'][0]['reason']=='inputs_superseded'
        assert connection.execute('SELECT state FROM compute_jobs').fetchone()[0]=='superseded'


def test_recovery_write_postcondition_rejects_silently_ignored_update():
    with recovery_database() as connection:
        task = spec()
        enqueue(connection, task)
        batch(connection, claim(ClaimIntent('claim','worker','mock-release',lease_ms=1000),1000))
        connection.execute('CREATE TRIGGER ignored_recovery BEFORE UPDATE ON compute_jobs BEGIN SELECT RAISE(IGNORE); END')
        connection.commit()
        with pytest.raises(sqlite3.DatabaseError):
            recover(connection,RecoveryIntent('recover'),2000)
        assert connection.execute('SELECT COUNT(*) FROM compute_recovery_items').fetchone()[0]==0
        assert connection.execute('SELECT state FROM compute_jobs').fetchone()[0]=='running'
