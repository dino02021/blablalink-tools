"""Independent receipt-driven schema proof, before any coordinator integration."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import sqlite3

import pytest

from application.tasks import StateConflict
from coordination.d1_cas_contract import HeadCasIntent
from coordination.receipt_d1_contract import SCHEMA_STATEMENTS,BEFORE_TRIGGER,AFTER_TRIGGER,build_receipt_cas_batch,verify_receipt_cas_result


@pytest.fixture
def database(tmp_path):
    path=tmp_path/'receipt.sqlite'
    with sqlite3.connect(path) as connection:
        for statement in SCHEMA_STATEMENTS:connection.execute(statement)
    return path


def intent(request='request',revision=0,version='a'):
    return HeadCasIntent(request,'mock_union','healthcheck',revision,version*64,'mock-release','actor')


def apply(path,operation):
    connection=sqlite3.connect(path,isolation_level=None,timeout=5)
    connection.row_factory=sqlite3.Row
    try:
        connection.execute('BEGIN IMMEDIATE')
        results=[]
        for statement in build_receipt_cas_batch(operation):
            cursor=connection.execute(statement.sql,statement.parameters)
            rows=[dict(row) for row in cursor.fetchall()] if cursor.description else []
            results.append({'success':True,'results':rows})
        connection.commit()
        return verify_receipt_cas_result(operation,results)
    except BaseException:
        connection.rollback()
        raise
    finally:connection.close()


def test_receipt_head_and_replay_are_one_atomic_decision(database):
    first=intent()
    assert apply(database,first)==first.receipt
    assert apply(database,intent('new',1,'b'))['revision']==2
    assert apply(database,first)==first.receipt
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT revision,version_id FROM contract_heads').fetchall()==[(2,'b'*64)]
        assert connection.execute('SELECT COUNT(*) FROM contract_receipts').fetchone()[0]==2


@pytest.mark.parametrize('change',['stale','request','actor','code'])
def test_conflicting_intents_never_change_head_or_receipt(database,change):
    apply(database,intent())
    operation=intent('other',0,'b') if change=='stale' else intent(version='b') if change=='request' else replace(intent(),**{'actor_id':'other'} if change=='actor' else {'code_sha':'other'})
    with pytest.raises(sqlite3.IntegrityError):apply(database,operation)
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT revision FROM contract_heads').fetchone()[0]==1
        assert connection.execute('SELECT COUNT(*) FROM contract_receipts').fetchone()[0]==1


@pytest.mark.parametrize('trigger',['contract_receipt_before','contract_receipt_after'])
def test_missing_trigger_cannot_issue_a_false_success_receipt(database,trigger):
    with sqlite3.connect(database) as connection:connection.execute('DROP TRIGGER '+trigger)
    with pytest.raises(sqlite3.DatabaseError):apply(database,intent())
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT COUNT(*) FROM contract_heads').fetchone()[0]==0
        assert connection.execute('SELECT COUNT(*) FROM contract_receipts').fetchone()[0]==0


def test_changed_trigger_definition_is_rejected_without_receipt(database):
    with sqlite3.connect(database) as connection:
        connection.execute('DROP TRIGGER contract_receipt_after')
        connection.execute("CREATE TRIGGER contract_receipt_after AFTER INSERT ON contract_receipts BEGIN SELECT 1; END")
    with pytest.raises(sqlite3.DatabaseError):apply(database,intent())
    with sqlite3.connect(database) as connection:assert connection.execute('SELECT COUNT(*) FROM contract_receipts').fetchone()[0]==0


def test_ignored_head_insert_rolls_back_the_original_receipt(database):
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TRIGGER ignore_head BEFORE INSERT ON contract_heads BEGIN SELECT RAISE(IGNORE); END')
    with pytest.raises(sqlite3.DatabaseError,match='malformed JSON'):apply(database,intent())
    # Even an internal write bypassing the client schema guard cannot leave a
    # receipt behind when its nested head write is ignored.
    with sqlite3.connect(database) as connection:
        operation=intent()
        with pytest.raises(sqlite3.IntegrityError,match='head_write_unproven'):
            connection.execute('INSERT INTO contract_receipts VALUES(?,?,?,?,?,?,?,?,?)',tuple(operation.receipt.values()))
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT COUNT(*) FROM contract_heads').fetchone()[0]==0
        assert connection.execute('SELECT COUNT(*) FROM contract_receipts').fetchone()[0]==0


@pytest.mark.parametrize('scope',['global','mock_union'])
def test_scoped_or_global_barrier_blocks_new_work_but_allows_exact_replay(database,scope):
    first=intent()
    apply(database,first)
    with sqlite3.connect(database) as connection:connection.execute('INSERT INTO contract_barriers VALUES(?,1)',(scope,))
    assert apply(database,first)==first.receipt
    with pytest.raises(sqlite3.IntegrityError,match='scope_barrier'):apply(database,intent('new',1,'b'))


@pytest.mark.parametrize('same_request',[False,True])
def test_two_connections_race_without_duplicate_receipts_or_head_updates(database,same_request):
    def submit(operation):
        try:return apply(database,operation)
        except sqlite3.IntegrityError:return None
    first=intent()
    second=first if same_request else intent('another',version='b')
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(submit,(first,second)))
    assert sum(result is not None for result in results)==(2 if same_request else 1)
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT revision FROM contract_heads').fetchone()[0]==1
        assert connection.execute('SELECT COUNT(*) FROM contract_receipts').fetchone()[0]==1


def test_schema_is_without_rowid_and_trigger_text_is_exactly_pinned(database):
    with sqlite3.connect(database) as connection:
        rows=connection.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'").fetchall()
        assert dict(rows)=={'contract_receipt_before':BEFORE_TRIGGER,'contract_receipt_after':AFTER_TRIGGER}
        tables=connection.execute('PRAGMA table_list').fetchall()
        assert all(row[4]==1 for row in tables if row[1].startswith('contract_'))


def test_single_mutation_survives_lost_readback_and_retry_does_not_increment(database):
    from coordination.receipt_d1_contract import build_receipt_cas_mutation
    operation=intent()
    mutation=build_receipt_cas_mutation(operation)
    with sqlite3.connect(database,isolation_level=None) as connection:connection.execute(mutation.sql,mutation.parameters)
    # The client vanishes before fetching its receipt. The mutation already
    # committed both records atomically and needs no open cross-call transaction.
    assert apply(database,operation)==operation.receipt
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT revision FROM contract_heads').fetchone()[0]==1


def test_changed_table_definition_cannot_run_stale_triggers_or_issue_receipts(database):
    with sqlite3.connect(database) as connection:
        connection.execute('ALTER TABLE contract_heads ADD COLUMN unexpected TEXT')
    with pytest.raises(sqlite3.DatabaseError,match='malformed JSON'):apply(database,intent())
    with sqlite3.connect(database) as connection:assert connection.execute('SELECT COUNT(*) FROM contract_receipts').fetchone()[0]==0
