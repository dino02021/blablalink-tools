import sqlite3

import pytest

from application.tasks import TaskSpec
from coordination.d1_compute_queue import SCHEMA_STATEMENTS, ClaimIntent, SourcePin, active_claim, claim, enqueue_frozen
from coordination.d1_objects import SCHEMA_STATEMENTS as OBJECTS
from coordination.receipt_d1_contract import SCHEMA_STATEMENTS as HEADS
from coordination.d1_schema import D1Schema


def definition():
    return D1Schema((*OBJECTS, *HEADS, *SCHEMA_STATEMENTS))


def database():
    connection = sqlite3.connect(':memory:')
    connection.row_factory = sqlite3.Row
    for declaration in definition().bootstrap:
        if isinstance(declaration, str): connection.execute(declaration)
        else: connection.execute(declaration['sql'], declaration['parameters'])
    connection.commit()
    return connection


def spec(intent='capture', **kwargs):
    return TaskSpec('refresh_raid_history', 'mock_union', 'mock-release', intent_id=intent, **kwargs)


def seed_spec_metadata(connection, task):
    # SQL metadata fixture only; actual adapter tests use fully verified objects.
    connection.execute('INSERT OR IGNORE INTO object_transports VALUES(?,?,?,1)', (task.request_signature, task.request_signature, 'metadata-fixture'))
    connection.execute('INSERT OR IGNORE INTO object_heads VALUES(?,?)', (task.request_signature, task.request_signature))
    connection.commit()


def batch(connection, statements):
    connection.execute('BEGIN IMMEDIATE')
    try:
        results = [list(map(dict, connection.execute(statement.sql, statement.parameters).fetchall())) for statement in statements]
        connection.commit()
        return results
    except Exception:
        connection.rollback()
        raise


def enqueue(connection, task, request='enqueue', pins=()):
    seed_spec_metadata(connection, task)
    statements, signature = enqueue_frozen(request, task, pins, 1000)
    result = batch(connection, statements)
    assert result[-1][0]['signature'] == signature
    return result[-1][0]


def test_claim_replay_returns_original_job_generation_and_expiry_without_claiming_second_job():
    with database() as connection:
        first, second = spec('first', priority=10), spec('second')
        enqueue(connection, first, 'first')
        enqueue(connection, second, 'second')
        intent = ClaimIntent('claim', 'worker', 'mock-release')
        receipt = batch(connection, claim(intent, 1000))[-1][0]
        assert receipt['job_id'] == first.fingerprint and receipt['generation'] == 1 and receipt['expires_at_ms'] == 61000
        assert batch(connection, claim(intent, 2000))[-1] == [receipt]
        assert connection.execute('SELECT attempt FROM compute_jobs WHERE job_id=?', (first.fingerprint,)).fetchone()[0] == 1
        assert connection.execute('SELECT state FROM compute_jobs WHERE job_id=?', (second.fingerprint,)).fetchone()[0] == 'queued'
        assert batch(connection, (active_claim(intent, receipt, 60000),))[0] == [{'job_id': first.fingerprint}]
        assert batch(connection, (active_claim(intent, receipt, 61000),))[0] == []


def test_changed_claim_request_is_rejected_and_does_not_claim_another_job():
    with database() as connection:
        enqueue(connection, spec('one'), 'one')
        enqueue(connection, spec('two'), 'two')
        original = ClaimIntent('claim', 'worker', 'mock-release')
        batch(connection, claim(original, 1000))
        with pytest.raises(sqlite3.DatabaseError):
            batch(connection, claim(ClaimIntent('claim', 'other', 'mock-release'), 1000))
        assert connection.execute("SELECT COUNT(*) FROM compute_jobs WHERE state='running'").fetchone()[0] == 1


def test_empty_claim_has_no_persistent_write_and_can_later_find_new_work():
    with database() as connection:
        intent = ClaimIntent('idle', 'worker', 'mock-release')
        before = connection.total_changes
        assert batch(connection, claim(intent, 1000))[-1] == []
        assert connection.total_changes == before
        enqueue(connection, spec())
        assert len(batch(connection, claim(intent, 1000))[-1]) == 1


def test_dependencies_and_code_filter_are_checked_inside_claim():
    with database() as connection:
        parent = spec('parent')
        child = spec('child', dependencies=(parent.fingerprint,), priority=100)
        enqueue(connection, parent, 'parent')
        enqueue(connection, child, 'child')
        assert batch(connection, claim(ClaimIntent('wrong-code', 'worker', 'different'), 1000))[-1] == []
        receipt = batch(connection, claim(ClaimIntent('parent-claim', 'worker', 'mock-release'), 1000))[-1][0]
        assert receipt['job_id'] == parent.fingerprint
        assert batch(connection, claim(ClaimIntent('waiting', 'worker', 'mock-release'), 1000))[-1] == []
        connection.execute("UPDATE compute_jobs SET state='succeeded' WHERE job_id=?", (parent.fingerprint,))
        connection.commit()
        assert batch(connection, claim(ClaimIntent('child-claim', 'worker', 'mock-release'), 1000))[-1][0]['job_id'] == child.fingerprint


def test_source_head_aba_blocks_fresh_work_but_exact_enqueue_replay_stays_read_only():
    with database() as connection:
        task = spec(versions={'raw': 'b' * 64})
        pin = SourcePin('raw', 'mock_union', 'raw', 'b' * 64, 1, 'mock-release')
        connection.execute('INSERT INTO contract_heads VALUES(?,?,?,?,?)', ('mock_union', 'raw', 'b' * 64, 'mock-release', 1))
        connection.commit()
        receipt = enqueue(connection, task, pins=(pin,))
        connection.execute('UPDATE contract_heads SET revision=3')
        connection.commit()
        statements, _ = enqueue_frozen('enqueue', task, (pin,), 2000)
        assert batch(connection, statements)[-1] == [receipt]
        assert batch(connection, claim(ClaimIntent('stale', 'worker', 'mock-release'), 2000))[-1] == []
        with pytest.raises(sqlite3.DatabaseError):
            batch(connection, enqueue_frozen('fresh-request', task, (pin,), 2000)[0])


def test_generation_fence_survives_explicit_recovery_and_old_claim_receipt():
    with database() as connection:
        task = spec()
        enqueue(connection, task)
        old_intent = ClaimIntent('old', 'worker-old', 'mock-release', lease_ms=1000)
        old = batch(connection, claim(old_intent, 1000))[-1][0]
        # Recovery transition is a metadata fixture; recovery API remains pending.
        connection.execute("UPDATE compute_jobs SET state='retry_wait',owner=NULL,expires_at_ms=NULL,next_attempt_at_ms=2000")
        connection.commit()
        new = batch(connection, claim(ClaimIntent('new', 'worker-new', 'mock-release'), 2000))[-1][0]
        assert new['generation'] == 2
        assert batch(connection, claim(old_intent, 2000))[-1] == [old]
        assert batch(connection, (active_claim(old_intent, old, 2000),))[0] == []


@pytest.mark.parametrize('duration', [True, 0, 999, 300001, 1.5])
def test_claim_duration_is_bounded_exact_milliseconds(duration):
    with pytest.raises(ValueError):
        ClaimIntent('request', 'worker', 'mock-release', duration)


@pytest.mark.parametrize('command', ['recompute_healthcheck', 'publish_raid_plan', 'send_reports'])
def test_unimplemented_domain_or_side_effect_commands_are_not_silently_admitted(command):
    task = TaskSpec(command, 'mock_union', 'mock-release', intent_id='request')
    with pytest.raises(ValueError):
        enqueue_frozen('request', task, (), 1000)
