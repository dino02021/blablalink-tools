import json
import sys

import pytest

from adapters.mock_d1_workspace import create_mock_d1_workspace
from coordination.d1_migrations import (AdditiveMigrationPlan, apply_revision, begin_migration, bootstrap,
    finish_migration, read_revision, ready_guard, verify_revision_receipt)
from coordination.d1_schema import D1Schema
from coordination.d1_statement import D1Statement
from executors.local.emulator_process import run_d1_emulator
from local_tests.test_d1_migrations import definition

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='requires verified Windows SDK supervision')


def operation(statements):
    return {'kind': 'batch', 'statements': [statement.wire for statement in statements]}


def run(directory, plan, operations):
    receipt = run_d1_emulator({'schema_statements': [], 'operations': operations},
                             mock_directory=directory, schema_id=plan.schemas[0].schema_id)
    assert receipt['outcome'] == 'completed' and receipt['is_child_stopped'] is True
    result = json.loads(receipt['stdout'])
    assert result.get('outcome') != 'emulator_failed', result
    assert result['are_sdk_children_closed'] is True and result['network_providers'] == []
    return result['operations']


def test_actual_sdk_interrupted_upgrade_resumes_from_positive_receipt_and_rejects_old_epoch(tmp_path):
    plan = definition()
    directory = create_mock_d1_workspace(tmp_path / 'd1', plan.schemas[0].schema_id)
    first = run(directory, plan, [operation(bootstrap(plan)),
        operation((D1Statement("INSERT INTO mock_existing VALUES('mock-id','preserved')", ()),)),
        operation(begin_migration(plan, 0, 1)), operation(apply_revision(plan, 1, 2)),
        operation((ready_guard(plan, 0, 1), D1Statement("INSERT INTO mock_existing VALUES('stale','bad')", ())))])
    assert [item['outcome'] for item in first] == ['completed'] * 4 + ['rejected']
    # The first owned SDK has stopped: the next process reads persisted evidence.
    lookup = run(directory, plan, [operation((read_revision(1),))])
    assert verify_revision_receipt(plan, 1, lookup[0]['results'][0]['results'])['applied_epoch'] == 2
    second = run(directory, plan, [operation(apply_revision(plan, 2, 2)),
        operation(finish_migration(plan, 2)), operation(finish_migration(plan, 2)),
        operation((ready_guard(plan, 0, 1), D1Statement("INSERT INTO mock_existing VALUES('old','bad')", ()))),
        operation((ready_guard(plan, 2, 2), D1Statement('SELECT * FROM mock_existing', ())))])
    assert [item['outcome'] for item in second] == ['completed', 'completed', 'completed', 'rejected', 'completed']
    assert sum(result['meta']['rows_written'] for result in second[2]['results']) == 0
    assert second[-1]['results'][-1]['results'] == [{'id': 'mock-id', 'value': 'preserved'}]


def test_actual_sdk_parallel_migration_begin_owns_one_epoch_and_never_marks_ready_early(tmp_path):
    plan = definition()
    directory = create_mock_d1_workspace(tmp_path / 'd1', plan.schemas[0].schema_id)
    wire = [statement.wire for statement in begin_migration(plan, 0, 1)]
    results = run(directory, plan, [operation(bootstrap(plan)),
        {'kind': 'parallel_batches', 'batches': [wire, wire]},
        operation((D1Statement('SELECT revision,epoch,state FROM coordinator_schema_authority', ()),)),
        operation((ready_guard(plan, 0, 1),))])
    assert sorted(item['outcome'] for item in results[1]['batches']) == ['completed', 'rejected']
    assert results[2]['results'][0]['results'] == [{'revision': 0, 'epoch': 2, 'state': 'migrating'}]
    assert results[3]['outcome'] == 'rejected'


def test_actual_sdk_failed_revision_rolls_back_created_table_and_history(tmp_path):
    initial = definition().schemas[0]
    target = D1Schema((*initial.statements, 'CREATE TABLE mock_first(id TEXT)', 'CREATE TABLE mock_collision(id TEXT)'))
    plan = AdditiveMigrationPlan((initial, target))
    directory = create_mock_d1_workspace(tmp_path / 'd1', initial.schema_id)
    results = run(directory, plan, [operation(bootstrap(plan)), operation(begin_migration(plan, 0, 1)),
        operation((D1Statement('CREATE TABLE mock_collision(unexpected TEXT)', ()),)),
        operation(apply_revision(plan, 1, 2)),
        operation((D1Statement("SELECT COUNT(*) AS count FROM sqlite_master WHERE name='mock_first'", ()), read_revision(1)))])
    assert results[3]['outcome'] == 'rejected'
    assert results[4]['results'][0]['results'] == [{'count': 0}]
    assert results[4]['results'][1]['results'] == []
