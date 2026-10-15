"""Execute immutable batch statements in SQLite; explicitly not remote D1 proof."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import sqlite3

import pytest

from application.tasks import StateConflict
from coordination.d1_cas_contract import BoundStatement,HeadCasIntent,build_head_cas_batch,verify_head_cas_batch_result

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture
def database(tmp_path):
    path=tmp_path/'contract.sqlite'
    with sqlite3.connect(path) as connection:
        connection.executescript((ROOT/'coordination/d1_contracts/0001_cas_contract.sql').read_text(encoding='utf-8'))
    return path


def intent(request='request',revision=0,version='a'):
    return HeadCasIntent(request,'mock_union','healthcheck',revision,version*64,'mock-release','actor')


def execute_batch(path,operation,fail_at=None):
    connection=sqlite3.connect(path,isolation_level=None,timeout=5)
    connection.row_factory=sqlite3.Row
    try:
        connection.execute('BEGIN IMMEDIATE')
        results=[]
        for index,statement in enumerate(build_head_cas_batch(operation)):
            if index==fail_at:raise RuntimeError('injected_batch_failure')
            cursor=connection.execute(statement.sql,statement.parameters)
            rows=[dict(row) for row in cursor.fetchall()] if cursor.description else []
            results.append({'success':True,'results':rows})
        connection.commit()
        return results
    except BaseException:
        connection.rollback()
        raise
    finally:connection.close()


def test_first_cas_and_exact_replay_have_one_immutable_receipt(database):
    operation=intent()
    result=execute_batch(database,operation)
    assert verify_head_cas_batch_result(operation,result)==operation.receipt
    assert verify_head_cas_batch_result(operation,execute_batch(database,operation))==operation.receipt
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT revision,version_id FROM contract_heads').fetchall()==[(1,'a'*64)]
        assert connection.execute('SELECT COUNT(*) FROM contract_receipts').fetchone()[0]==1
        assert connection.execute('SELECT COUNT(*) FROM contract_cas_guards').fetchone()[0]==0


def test_zero_row_cas_and_wrong_request_signature_roll_back(database):
    execute_batch(database,intent())
    with pytest.raises(sqlite3.IntegrityError):execute_batch(database,intent('stale',version='b'))
    with pytest.raises(sqlite3.IntegrityError):execute_batch(database,intent(version='b'))
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT revision,version_id FROM contract_heads').fetchall()==[(1,'a'*64)]
        assert connection.execute('SELECT COUNT(*) FROM contract_receipts').fetchone()[0]==1


def test_lost_response_retry_after_new_head_never_rewinds_that_head(database):
    original=intent()
    execute_batch(database,original)  # caller loses response after commit
    execute_batch(database,intent('new-head',1,'b'))
    assert verify_head_cas_batch_result(original,execute_batch(database,original))==original.receipt
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT revision,version_id FROM contract_heads').fetchall()==[(2,'b'*64)]


@pytest.mark.parametrize('position',range(7))
def test_failure_at_any_statement_rolls_back_head_receipt_and_guard(database,position):
    with pytest.raises(RuntimeError,match='injected'):execute_batch(database,intent(),position)
    with sqlite3.connect(database) as connection:
        assert all(connection.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]==0 for table in ('contract_heads','contract_receipts','contract_cas_guards'))


@pytest.mark.parametrize('table',['contract_heads','contract_receipts'])
def test_silent_noop_write_is_caught_by_sql_guard_before_commit(database,table):
    with sqlite3.connect(database) as connection:
        connection.execute(f'CREATE TRIGGER ignore_write BEFORE INSERT ON {table} BEGIN SELECT RAISE(IGNORE); END')
    with pytest.raises(sqlite3.IntegrityError):execute_batch(database,intent())
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT COUNT(*) FROM contract_heads').fetchone()[0]==0
        assert connection.execute('SELECT COUNT(*) FROM contract_receipts').fetchone()[0]==0


@pytest.mark.parametrize('scope',['global','mock_union'])
def test_barrier_blocks_new_operations_but_exact_receipt_replay_is_read_only(database,scope):
    operation=intent()
    execute_batch(database,operation)
    with sqlite3.connect(database) as connection:connection.execute('INSERT INTO contract_barriers VALUES(?,1)',(scope,))
    assert verify_head_cas_batch_result(operation,execute_batch(database,operation))==operation.receipt
    with pytest.raises(sqlite3.IntegrityError):execute_batch(database,intent('blocked',1,'b'))


def test_two_real_connections_racing_same_base_have_exactly_one_winner(database):
    def compete(operation):
        try:return verify_head_cas_batch_result(operation,execute_batch(database,operation))
        except sqlite3.IntegrityError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(compete,(intent('a',version='a'),intent('b',version='b'))))
    assert sum(result is not None for result in results)==1


@pytest.mark.parametrize('change',['short','false_success','missing_receipt','wrong_receipt','bool_revision'])
def test_incomplete_transport_response_is_never_accepted_as_completed(database,change):
    operation=intent()
    results=execute_batch(database,operation)
    if change=='short':results.pop()
    elif change=='false_success':results[0]['success']=1
    elif change=='missing_receipt':results[5]['results']=[]
    elif change=='wrong_receipt':results[5]['results'][0]['code_sha']='wrong'
    else:results[5]['results'][0]['revision']=True
    with pytest.raises(StateConflict,match='unproven'):verify_head_cas_batch_result(operation,results)


def test_parameter_binding_keeps_identifier_text_out_of_sql(database):
    operation=replace(intent(),scope_id="mock'); DROP TABLE contract_heads; --")
    statements=build_head_cas_batch(operation)
    assert all(operation.scope_id not in statement.sql for statement in statements)
    assert verify_head_cas_batch_result(operation,execute_batch(database,operation))==operation.receipt


@pytest.mark.parametrize('field,value',[('expected_revision',True),('expected_revision',-1),('expected_revision',2**31-1),('version_id','bad'),('code_sha','x'*129)])
def test_invalid_intents_never_produce_statements(field,value):
    with pytest.raises(ValueError):replace(intent(),**{field:value})


def test_local_batch_profile_rejects_large_blob_and_implicit_bindings():
    for parameters in ((b'bytes',),('x'*4097,),('x'*2_000_001,), (True,), (2**53,), 'implicit'):
        with pytest.raises(ValueError):BoundStatement('SELECT ?',parameters)
    assert len(build_head_cas_batch(intent()))==7


def test_two_connections_replaying_same_request_both_observe_one_decision(database):
    operation=intent()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:verify_head_cas_batch_result(operation,execute_batch(database,operation)),range(2)))
    assert results==[operation.receipt,operation.receipt]
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT revision FROM contract_heads').fetchone()[0]==1


def test_corrupt_existing_receipt_cannot_bypass_postcondition_checks(database):
    operation=intent()
    execute_batch(database,operation)
    with sqlite3.connect(database) as connection:connection.execute("UPDATE contract_receipts SET actor_id='corrupt'")
    with pytest.raises(sqlite3.IntegrityError):execute_batch(database,operation)
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT COUNT(*) FROM contract_cas_guards').fetchone()[0]==0
        assert connection.execute('SELECT revision FROM contract_heads').fetchone()[0]==1
