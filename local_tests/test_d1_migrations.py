import sqlite3

import pytest

from coordination.d1_migrations import (AdditiveMigrationPlan, LEDGER, apply_revision, begin_migration,
    bootstrap, finish_migration, history_guard, read_revision, ready_guard, verify_revision_receipt)
from coordination.d1_schema import D1Schema
from coordination.d1_statement import D1Statement


def definition():
    first = D1Schema(('CREATE TABLE mock_existing(id TEXT PRIMARY KEY,value TEXT NOT NULL) WITHOUT ROWID',))
    second = D1Schema((*first.statements, 'CREATE TABLE mock_added(id TEXT PRIMARY KEY,value TEXT) WITHOUT ROWID'))
    third = D1Schema((*second.statements, 'CREATE INDEX mock_added_value ON mock_added(value)'))
    return AdditiveMigrationPlan((first, second, third))


def batch(connection, statements):
    connection.execute('BEGIN')
    try:
        results = []
        for item in statements:
            cursor = connection.execute(item.sql, item.parameters)
            results.append([dict(row) for row in cursor.fetchall()] if cursor.description else [])
        connection.execute('COMMIT')
        return results
    except BaseException:
        connection.execute('ROLLBACK')
        raise


def database(plan):
    connection = sqlite3.connect(':memory:', isolation_level=None)
    connection.row_factory = sqlite3.Row
    batch(connection, bootstrap(plan))
    connection.execute("INSERT INTO mock_existing VALUES('mock-id','preserved')")
    return connection


def test_additive_revisions_preserve_rows_and_close_readiness_between_batches():
    plan = definition()
    with database(plan) as connection:
        batch(connection, (ready_guard(plan, 0, 1),))
        batch(connection, begin_migration(plan, 0, 1))
        with pytest.raises(sqlite3.DatabaseError):
            batch(connection, (ready_guard(plan, 0, 1), D1Statement("INSERT INTO mock_existing VALUES('stale','bad')", ())))
        batch(connection, apply_revision(plan, 1, 2))
        receipt = batch(connection, (read_revision(1),))[-1]
        assert verify_revision_receipt(plan, 1, receipt)['applied_epoch'] == 2
        # Restart recovery checks a positive receipt instead of replaying CREATE.
        with pytest.raises(sqlite3.DatabaseError):
            batch(connection, apply_revision(plan, 1, 2))
        batch(connection, apply_revision(plan, 2, 2))
        with pytest.raises(sqlite3.DatabaseError):
            batch(connection, (ready_guard(plan, 2, 2),))
        batch(connection, finish_migration(plan, 2))
        changes = connection.total_changes
        batch(connection, finish_migration(plan, 2))
        assert connection.total_changes == changes
        batch(connection, (ready_guard(plan, 2, 2),))
        assert [tuple(row) for row in connection.execute('SELECT * FROM mock_existing')] == [('mock-id', 'preserved')]
        with pytest.raises(sqlite3.DatabaseError):
            batch(connection, (ready_guard(plan, 0, 1),))


def test_later_release_extends_verified_history_and_increments_epoch_once():
    plan = definition()
    short = AdditiveMigrationPlan(plan.schemas[:2])
    assert short.checksums == plan.checksums[:2]
    with database(short) as connection:
        batch(connection, begin_migration(short, 0, 1))
        batch(connection, apply_revision(short, 1, 2))
        batch(connection, finish_migration(short, 2))
        batch(connection, begin_migration(plan, 1, 2))
        batch(connection, apply_revision(plan, 2, 3))
        batch(connection, finish_migration(plan, 3))
        assert batch(connection, (read_revision(1),))[-1][0]['applied_epoch'] == 2
        assert batch(connection, (read_revision(2),))[-1][0]['applied_epoch'] == 3


def test_partial_step_error_rolls_back_earlier_ddl_and_does_not_write_history():
    initial = definition().schemas[0]
    target = D1Schema((*initial.statements, 'CREATE TABLE mock_first(id TEXT)', 'CREATE TABLE mock_collision(id TEXT)'))
    plan = AdditiveMigrationPlan((initial, target))
    with database(plan) as connection:
        batch(connection, begin_migration(plan, 0, 1))
        connection.execute('CREATE TABLE mock_collision(unexpected TEXT)')
        with pytest.raises(sqlite3.DatabaseError):
            batch(connection, apply_revision(plan, 1, 2))
        assert not connection.execute("SELECT 1 FROM sqlite_master WHERE name='mock_first'").fetchone()
        assert batch(connection, (read_revision(1),))[-1] == []
        row = connection.execute('SELECT revision,state,epoch FROM coordinator_schema_authority').fetchone()
        assert tuple(row) == (0, 'migrating', 2)


def test_changed_plan_skipped_revision_and_competing_begin_cannot_take_ownership():
    plan = definition()
    changed = AdditiveMigrationPlan((plan.schemas[0], D1Schema((*plan.schemas[0].statements, 'CREATE TABLE changed(id TEXT)'))))
    with database(plan) as connection:
        batch(connection, begin_migration(plan, 0, 1))
        for statements in (begin_migration(changed, 0, 1), apply_revision(changed, 1, 2),
                           apply_revision(plan, 2, 2), finish_migration(plan, 2), apply_revision(plan, 1, 3)):
            with pytest.raises(sqlite3.DatabaseError):
                batch(connection, statements)
        batch(connection, apply_revision(plan, 1, 2))
        receipt = batch(connection, (read_revision(1),))[-1]
        with pytest.raises(ValueError):
            verify_revision_receipt(changed, 1, receipt)


def test_history_cannot_be_rewritten_and_unexpected_future_receipt_blocks_migration():
    plan = definition()
    with database(plan) as connection:
        for sql in ('DELETE FROM coordinator_schema_history', "UPDATE coordinator_schema_history SET checksum='changed'"):
            with pytest.raises(sqlite3.DatabaseError):
                connection.execute(sql)
        connection.execute('INSERT INTO coordinator_schema_history VALUES(1,?,?,?,1)', ('b' * 64, 'c' * 64, 'd' * 64))
        with pytest.raises(sqlite3.DatabaseError):
            batch(connection, begin_migration(plan, 0, 1))
        with pytest.raises(sqlite3.DatabaseError):
            batch(connection, (ready_guard(plan, 0, 1),))
        assert tuple(connection.execute('SELECT state,epoch FROM coordinator_schema_authority').fetchone()) == ('ready', 1)


def test_ready_pin_rejects_rearranged_checksum_chain_with_same_final_schema():
    initial = definition().schemas[0]
    first = 'CREATE TABLE mock_added_first(id TEXT)'
    second = 'CREATE TABLE mock_added_second(id TEXT)'
    final = D1Schema((*initial.statements, first, second))
    plan = AdditiveMigrationPlan((initial, D1Schema((*initial.statements, first)), final))
    changed = AdditiveMigrationPlan((initial, D1Schema((*initial.statements, second)), final))
    assert plan.schemas[-1].schema_id == changed.schemas[-1].schema_id
    assert plan.checksums[-1] != changed.checksums[-1]
    with database(plan) as connection:
        batch(connection, begin_migration(plan, 0, 1))
        for revision in (1, 2):
            batch(connection, apply_revision(plan, revision, 2))
        batch(connection, finish_migration(plan, 2))
        with pytest.raises(sqlite3.DatabaseError):
            batch(connection, (ready_guard(changed, 2, 2),))


def test_unknown_ledger_trigger_rejects_migration_before_any_state_change():
    plan = definition()
    with database(plan) as connection:
        connection.execute('CREATE TRIGGER mock_unknown AFTER UPDATE ON coordinator_schema_authority BEGIN SELECT 1; END')
        with pytest.raises(sqlite3.DatabaseError):
            batch(connection, begin_migration(plan, 0, 1))
        assert tuple(connection.execute('SELECT state,epoch FROM coordinator_schema_authority').fetchone()) == ('ready', 1)


@pytest.mark.parametrize('bad', [(), (definition().schemas[0],), (definition().schemas[1], definition().schemas[0]),
    (definition().schemas[0], definition().schemas[0]), (definition().schemas[0], LEDGER)])
def test_invalid_nonadditive_or_ledger_owned_plans_rejected(bad):
    with pytest.raises(ValueError):
        AdditiveMigrationPlan(bad)


def test_maximum_bounded_chain_has_complete_receipts_under_control_limits():
    schemas = [definition().schemas[0]]
    for revision in range(1, 17):
        schemas.append(D1Schema((*schemas[-1].statements, f'CREATE TABLE mock_revision_{revision}(id TEXT)')))
    plan = AdditiveMigrationPlan(schemas)
    with database(plan) as connection:
        batch(connection, begin_migration(plan, 0, 1))
        for revision in range(1, 17):
            batch(connection, apply_revision(plan, revision, 2))
        batch(connection, finish_migration(plan, 2))
        assert len(history_guard(plan, 16).parameters) == 68
    with pytest.raises(ValueError):
        AdditiveMigrationPlan((*schemas, schemas[-1]))


@pytest.mark.parametrize('epoch', [True, 0, -1, 2 ** 31, 1.1])
def test_invalid_epoch_is_rejected_before_sql(epoch):
    with pytest.raises(ValueError):
        begin_migration(definition(), 0, epoch)


def test_positive_receipt_is_not_readiness_or_new_work_authorization():
    plan = definition()
    with database(plan) as connection:
        rows = batch(connection, (read_revision(0),))[-1]
        assert verify_revision_receipt(plan, 0, rows)['revision'] == 0
        for corrupted in ([], [{**rows[0], 'revision': False}], [{**rows[0], 'applied_epoch': True}],
                          [{**rows[0], 'checksum': 'f' * 64}], [{**rows[0], 'extra': 1}]):
            with pytest.raises(ValueError):
                verify_revision_receipt(plan, 0, corrupted)
