"""Run the receipt-driven prototype through the actual pinned local D1 binding."""

from dataclasses import asdict
import json
import sys

import pytest

from coordination.d1_cas_contract import HeadCasIntent
from coordination.receipt_d1_contract import SCHEMA_STATEMENTS,build_receipt_cas_batch,build_receipt_cas_mutation,verify_receipt_cas_result
from executors.local.emulator_process import run_d1_emulator

pytestmark=pytest.mark.skipif(sys.platform!='win32',reason='actual local D1 runtime requires the verified Windows supervision profile')


def intent(request='request',revision=0,version='a'):
    return HeadCasIntent(request,'mock_union','healthcheck',revision,version*64,'mock-release','actor')


def batch(operation):return {'kind':'batch','statements':[asdict(statement) for statement in build_receipt_cas_batch(operation)]}


def query(sql,parameters=()):return {'kind':'query','sql':sql,'parameters':list(parameters)}


def run(operations):
    receipt=run_d1_emulator({'schema_statements':SCHEMA_STATEMENTS,'operations':operations})
    assert receipt['outcome']=='completed' and receipt['is_child_stopped']
    observed=json.loads(receipt['stdout'])
    assert observed['is_mock'] and observed['are_sdk_children_closed'] and observed['network_providers']==[]
    return observed['operations']


def test_actual_receipt_trigger_cas_writes_two_rows_and_replay_writes_zero():
    operation=intent()
    results=run([batch(operation),batch(operation)])
    assert verify_receipt_cas_result(operation,results[0]['results'])==operation.receipt
    assert verify_receipt_cas_result(operation,results[1]['results'])==operation.receipt
    assert sum(row['meta']['rows_written'] for row in results[0]['results'])==2
    assert sum(row['meta']['rows_written'] for row in results[1]['results'])==0


@pytest.mark.parametrize('same_request',[False,True])
def test_real_parallel_d1_batches_keep_exactly_one_head_decision(same_request):
    first=intent()
    second=first if same_request else intent('other',version='b')
    results=run([{'kind':'parallel_batches','batches':[batch(first)['statements'],batch(second)['statements']]},
        query('SELECT revision,(SELECT COUNT(*) FROM contract_receipts) AS receipts FROM contract_heads')])
    assert sum(item['outcome']=='completed' for item in results[0]['batches'])==(2 if same_request else 1)
    assert results[1]['result']['results']==[{'revision':1,'receipts':1}]


def test_stale_conflict_barrier_and_old_replay_preserve_the_new_head():
    first=intent()
    results=run([batch(first),batch(intent('stale',0,'b')),batch(intent('new',1,'b')),batch(first),
        query('INSERT INTO contract_barriers VALUES(?,1)',('mock_union',)),batch(intent('blocked',2,'c')),
        query('SELECT version_id,revision,(SELECT COUNT(*) FROM contract_receipts) AS receipts FROM contract_heads')])
    assert results[1]['outcome']=='rejected' and results[5]['outcome']=='rejected'
    assert verify_receipt_cas_result(first,results[3]['results'])==first.receipt
    assert results[-1]['result']['results']==[{'version_id':'b'*64,'revision':2,'receipts':2}]


@pytest.mark.parametrize('trigger',['contract_receipt_before','contract_receipt_after'])
def test_actual_d1_missing_trigger_is_rejected_before_receipt_or_head_write(trigger):
    results=run([query('DROP TRIGGER '+trigger),batch(intent()),
        query('SELECT (SELECT COUNT(*) FROM contract_heads) AS heads,(SELECT COUNT(*) FROM contract_receipts) AS receipts')])
    assert results[0]['outcome']=='completed' and results[1]['outcome']=='rejected'
    assert results[-1]['result']['results']==[{'heads':0,'receipts':0}]


def test_actual_d1_changed_table_shape_fails_closed():
    results=run([query('ALTER TABLE contract_heads ADD COLUMN unexpected TEXT'),batch(intent()),
        query('SELECT COUNT(*) AS receipts FROM contract_receipts')])
    assert results[0]['outcome']=='completed' and results[1]['outcome']=='rejected'
    assert results[-1]['result']['results']==[{'receipts':0}]


def test_actual_d1_extra_trigger_is_not_treated_as_known_schema():
    results=run([query('CREATE TRIGGER extra_head BEFORE INSERT ON contract_heads BEGIN SELECT 1; END'),batch(intent()),
        query('SELECT COUNT(*) AS heads FROM contract_heads')])
    assert results[0]['outcome']=='completed' and results[1]['outcome']=='rejected'
    assert results[-1]['result']['results']==[{'heads':0}]


def test_lost_readback_after_single_mutation_recovers_without_open_transaction():
    operation=intent()
    mutation=build_receipt_cas_mutation(operation)
    results=run([query(mutation.sql,mutation.parameters),batch(operation),
        query('SELECT revision,(SELECT COUNT(*) FROM contract_receipts) AS receipts FROM contract_heads')])
    assert results[0]['outcome']=='completed'
    assert verify_receipt_cas_result(operation,results[1]['results'])==operation.receipt
    assert sum(row['meta']['rows_written'] for row in results[1]['results'])==0
    assert results[-1]['result']['results']==[{'revision':1,'receipts':1}]


def test_same_fixture_comparison_keeps_semantics_with_lower_write_cost():
    from executors.local.d1_receipt_runtime import run_receipt_d1_comparison
    result=run_receipt_d1_comparison()
    assert result['is_comparison_passed'] and result['is_production_accepted'] is False
    assert result['baseline_usage']['new_cas']['rows_written']==9
    assert result['receipt_driven_usage']['new_cas']['rows_written']==2
    assert result['baseline_usage']['batch_replay']['rows_written']==5
    assert result['receipt_driven_usage']['batch_replay']['rows_written']==0
    assert result['baseline_details']['are_sdk_children_closed'] and result['receipt_details']['are_sdk_children_closed']
    assert result['is_complete_account_usage'] is False
