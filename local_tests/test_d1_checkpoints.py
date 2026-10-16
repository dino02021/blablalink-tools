from dataclasses import replace
import hashlib
import sqlite3

import pytest

from application.tasks import ComputeLease
from coordination.d1_checkpoints import (SCHEMA_STATEMENTS, CheckpointIntent, CheckpointVersion,
    build_checkpoint, verify_checkpoint_receipt)
from domain.snapshots import canonical_json
from local_tests.test_d1_compute_queue import batch, database
from local_tests.test_d1_lease_actions import running, sealed_result_metadata


def checkpoint_database():
    connection = database()
    for statement in SCHEMA_STATEMENTS:
        connection.execute(statement)
    connection.commit()
    return connection


def test_checkpoint_revision_cas_and_old_request_replay_do_not_rewind_newer_head():
    with checkpoint_database() as connection:
        lease = running(connection)
        first_id = sealed_result_metadata(connection, 'c' * 64)
        second_id = sealed_result_metadata(connection, 'd' * 64)
        first = CheckpointIntent('first', lease, first_id, 0)
        original = batch(connection, build_checkpoint(first, 1100))[-1]
        assert verify_checkpoint_receipt(first, original)['revision'] == 1
        second = CheckpointIntent('second', lease, second_id, 1)
        assert verify_checkpoint_receipt(second, batch(connection, build_checkpoint(second, 1200))[-1])['revision'] == 2
        assert batch(connection, build_checkpoint(first, 5000))[-1] == original
        assert connection.execute('SELECT object_id,revision FROM compute_checkpoints').fetchone()[:] == (second_id, 2)
        with pytest.raises(sqlite3.DatabaseError):
            batch(connection, build_checkpoint(CheckpointIntent('stale', lease, first_id, 1), 1300))
        with pytest.raises(sqlite3.DatabaseError):
            batch(connection, build_checkpoint(replace(first, object_id=second_id), 1300))


@pytest.mark.parametrize('problem', ['unsealed', 'expired', 'wrong_generation', 'wrong_owner'])
def test_invalid_checkpoint_cannot_move_head_or_record_success(problem):
    with checkpoint_database() as connection:
        lease = running(connection)
        identity = 'c' * 64 if problem == 'unsealed' else sealed_result_metadata(connection)
        now = 2000 if problem == 'expired' else 1100
        if problem == 'wrong_generation': lease = ComputeLease(lease.job_id, lease.worker_id, lease.code_sha, 2)
        if problem == 'wrong_owner': lease = ComputeLease(lease.job_id, 'other', lease.code_sha, 1)
        with pytest.raises(sqlite3.DatabaseError):
            batch(connection, build_checkpoint(CheckpointIntent('request', lease, identity, 0), now))
        assert connection.execute('SELECT COUNT(*) FROM compute_checkpoints').fetchone()[0] == 0
        assert connection.execute('SELECT COUNT(*) FROM compute_checkpoint_receipts').fetchone()[0] == 0


def test_ignored_checkpoint_update_is_rejected_by_write_postcondition():
    with checkpoint_database() as connection:
        lease = running(connection)
        identity = sealed_result_metadata(connection)
        connection.execute('CREATE TRIGGER ignored_checkpoint BEFORE INSERT ON compute_checkpoints BEGIN SELECT RAISE(IGNORE); END')
        connection.commit()
        with pytest.raises(sqlite3.DatabaseError):
            batch(connection, build_checkpoint(CheckpointIntent('request', lease, identity, 0), 1100))
        assert connection.execute('SELECT COUNT(*) FROM compute_checkpoint_receipts').fetchone()[0] == 0


def test_checkpoint_version_payload_copies_keep_content_identity():
    serialized = canonical_json({'offset': 10})
    identity = hashlib.sha256(serialized.encode()).hexdigest()
    version = CheckpointVersion('a'*64, identity, 'mock-release', 1, 2, serialized)
    value = version.payload
    value['offset'] = 0
    assert version.payload == {'offset': 10}
    with pytest.raises(ValueError):
        replace(version, revision=True)
    with pytest.raises(ValueError):
        replace(version, serialized=canonical_json({'offset': 0}))
