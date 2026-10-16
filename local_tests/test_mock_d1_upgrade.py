from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import sys
import threading

import pytest

from adapters.local_d1_versioned_session import LocalD1VersionedSession
from adapters.mock_d1_backup import create_mock_d1_backup, restore_mock_d1_backup
from adapters.mock_d1_maintenance import MaintenanceIntent
from adapters.mock_d1_upgrade import _verify_state, upgrade_mock_d1_workspace
from application.tasks import StateConflict
from coordination.d1_compute_queue import SCHEMA_STATEMENTS as COMPUTE
from coordination.d1_migrations import AdditiveMigrationPlan, apply_revision, begin_migration, bootstrap, finish_migration
from coordination.d1_objects import SCHEMA_STATEMENTS as OBJECTS
from coordination.d1_schema import D1Schema
from coordination.d1_statement import D1Statement
from coordination.receipt_d1_contract import SCHEMA_STATEMENTS as HEADS
from local_tests.test_d1_migrations import batch, definition


def metadata(connection):
    return ([dict(row) for row in connection.execute('SELECT * FROM coordinator_schema_authority')],
            [dict(row) for row in connection.execute('SELECT * FROM coordinator_schema_history ORDER BY revision')])


@pytest.mark.parametrize('stage', ['ready', 'begin', 'step1', 'step2', 'finish'])
def test_authority_phase_and_complete_history_are_verified_together(stage):
    intent = MaintenanceIntent(definition(), 0, 1)
    with sqlite3.connect(':memory:', isolation_level=None) as connection:
        connection.row_factory = sqlite3.Row
        batch(connection, bootstrap(intent.plan))
        operations = [begin_migration(intent.plan, 0, 1), apply_revision(intent.plan, 1, 2),
                      apply_revision(intent.plan, 2, 2), finish_migration(intent.plan, 2)]
        index = ['ready', 'begin', 'step1', 'step2', 'finish'].index(stage)
        for statements in operations[:index]:
            batch(connection, statements)
        state = _verify_state(intent, *metadata(connection))
        assert state.phase == ('ready_before' if index == 0 else 'complete' if index == 4 else 'migrating')
        assert state.revision == min(max(index - 1, 0), 2)


@pytest.mark.parametrize('corruption', ['authority_count', 'singleton_bool', 'revision_bool', 'epoch_bool',
    'checksum', 'missing_history', 'applied_epoch', 'unexpected_field', 'phase', 'wrong_target'])
def test_malformed_phase_or_history_never_authorizes_marker_update(corruption):
    intent = MaintenanceIntent(definition(), 0, 1)
    with sqlite3.connect(':memory:', isolation_level=None) as connection:
        connection.row_factory = sqlite3.Row
        batch(connection, bootstrap(intent.plan))
        rows, history = metadata(connection)
    if corruption == 'authority_count': rows = []
    elif corruption == 'singleton_bool': rows[0]['singleton'] = True
    elif corruption == 'revision_bool': rows[0]['revision'] = False
    elif corruption == 'epoch_bool': rows[0]['epoch'] = True
    elif corruption == 'checksum': history[0]['checksum'] = 'b' * 64
    elif corruption == 'missing_history': history = []
    elif corruption == 'applied_epoch': history[0]['applied_epoch'] = 2
    elif corruption == 'unexpected_field': rows[0]['unknown'] = 1
    elif corruption == 'phase': rows[0]['state'] = 'unknown'
    else: rows[0]['target_revision'] = False
    with pytest.raises(StateConflict):
        _verify_state(intent, rows, history)


def test_historical_epoch_gap_is_not_a_valid_release_prefix():
    plan = definition()
    intent = MaintenanceIntent(plan, 1, 3)
    with sqlite3.connect(':memory:', isolation_level=None) as connection:
        connection.row_factory = sqlite3.Row
        batch(connection, bootstrap(plan))
        batch(connection, begin_migration(plan, 0, 1))
        batch(connection, apply_revision(plan, 1, 2))
        rows, history = metadata(connection)
    rows[0].update(epoch=3, state='ready', plan_id=None, target_revision=None, target_schema_id=None)
    history[1]['applied_epoch'] = 3
    with pytest.raises(StateConflict, match='epoch'):
        _verify_state(intent, rows, history)


def create(directory, plan):
    session = LocalD1VersionedSession(directory, plan, 0, 1, is_create=True)
    session.execute((D1Statement("INSERT INTO mock_existing VALUES('mock-id','preserved')", ()),))
    return session


native = pytest.mark.skipif(sys.platform != 'win32', reason='requires verified Windows SDK supervision')


@native
def test_actual_upgrade_preserves_rows_reopens_new_session_and_replay_has_zero_writes(tmp_path, monkeypatch):
    from adapters import mock_d1_upgrade
    directory = tmp_path / 'd1'
    intent = MaintenanceIntent(definition(), 0, 1)
    old = create(directory, intent.plan)
    receipt = upgrade_mock_d1_workspace(directory, intent)
    assert receipt['epoch'] == 2 and receipt['target_revision'] == 2
    assert not (directory / 'maintenance-lock.json').exists()
    reopened = LocalD1VersionedSession(directory, intent.plan, 2, 2)
    assert reopened.execute((D1Statement('SELECT * FROM mock_existing', ()),))[0]['results'] == [{'id': 'mock-id', 'value': 'preserved'}]
    with pytest.raises(ValueError, match='marker'):
        old.execute((D1Statement('SELECT 1', ()),))
    original = mock_d1_upgrade.run_d1_maintenance_batch
    observed = []
    def invoke(*args, **kwargs):
        result = original(*args, **kwargs)
        observed.append(json.loads(result['stdout']))
        return result
    monkeypatch.setattr(mock_d1_upgrade, 'run_d1_maintenance_batch', invoke)
    assert upgrade_mock_d1_workspace(directory, intent) == receipt
    assert sum(result['meta']['rows_written'] for output in observed for operation in output['operations'] for result in operation['results']) == 0


@native
@pytest.mark.parametrize('stage', ['begin', 'step', 'finish', 'before_marker', 'after_marker', 'lost_journal'])
def test_actual_interruption_resumes_committed_progress_without_replaying_ddl(tmp_path, stage):
    from adapters import mock_d1_upgrade
    intent = MaintenanceIntent(definition(), 0, 1)
    directory = tmp_path / 'd1'
    create(directory, intent.plan)
    original_invoke = mock_d1_upgrade._invoke
    original_write = mock_d1_upgrade._atomic_owned_write
    fault_statements = {'begin': begin_migration(intent.plan, 0, 1), 'step': apply_revision(intent.plan, 1, 2),
                        'lost_journal': apply_revision(intent.plan, 1, 2),
                        'finish': finish_migration(intent.plan, 2)}.get(stage)
    def invoke(capability, statements):
        result = original_invoke(capability, statements)
        if fault_statements is not None and statements == fault_statements:
            raise OSError('mock committed reply lost')
        return result
    def write(path, content):
        if stage == 'before_marker':
            raise OSError('mock marker write interrupted')
        original_write(path, content)
        raise OSError('mock marker reply lost')
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(mock_d1_upgrade, '_invoke', invoke)
        if stage.endswith('marker'):
            patch.setattr(mock_d1_upgrade, '_atomic_owned_write', write)
        with pytest.raises(OSError, match='mock'):
            upgrade_mock_d1_workspace(directory, intent)
    assert (directory / 'maintenance-lock.json').read_bytes() == intent.journal_bytes
    if stage == 'lost_journal':
        (directory / 'maintenance-lock.json').unlink()
    with pytest.raises(ValueError, match='maintenance' if stage != 'lost_journal' else 'outcome'):
        LocalD1VersionedSession(directory, intent.plan, 0, 1)
    calls = []
    def resumed(capability, statements):
        calls.append(statements)
        return original_invoke(capability, statements)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(mock_d1_upgrade, '_invoke', resumed)
        receipt = upgrade_mock_d1_workspace(directory, intent)
    if stage != 'begin':
        assert apply_revision(intent.plan, 1, 2) not in calls
    if stage in ('finish', 'before_marker', 'after_marker'):
        assert finish_migration(intent.plan, 2) not in calls
    assert receipt['epoch'] == 2 and not (directory / 'maintenance-lock.json').exists()
    session = LocalD1VersionedSession(directory, intent.plan, 2, 2)
    history = session.execute((D1Statement('SELECT revision,applied_epoch FROM coordinator_schema_history ORDER BY revision', ()),))
    assert history[0]['results'] == [{'revision': 0, 'applied_epoch': 1}, {'revision': 1, 'applied_epoch': 2}, {'revision': 2, 'applied_epoch': 2}]


@native
def test_actual_busy_hosted_lease_and_scope_barrier_do_not_begin_or_create_journal(tmp_path):
    base = D1Schema((*OBJECTS, *HEADS, *COMPUTE))
    plan = AdditiveMigrationPlan((base, D1Schema((*base.statements, 'CREATE TABLE mock_future(id TEXT)'))))
    intent = MaintenanceIntent(plan, 0, 1)
    directory = tmp_path / 'd1'
    session = LocalD1VersionedSession(directory, plan, 0, 1, is_create=True)
    session.execute((D1Statement("INSERT INTO compute_jobs(job_id,spec_id,input_signature,command,scope_id,family,code_sha,executor_type,priority,max_attempts,state,owner,expires_at_ms,next_attempt_at_ms,created_at_ms,updated_at_ms) VALUES('mock-job','mock-spec','mock-input','refresh_raid_history','mock_union','raid','mock-release','actions',0,3,'running','old-worker',1,0,0,0)", ()),))
    marker = (directory / 'mock-d1.json').read_bytes()
    with pytest.raises(StateConflict):
        upgrade_mock_d1_workspace(directory, intent)
    assert not (directory / 'maintenance-lock.json').exists()
    assert (directory / 'mock-d1.json').read_bytes() == marker
    session.execute((D1Statement("UPDATE compute_jobs SET state='retry_wait',owner=NULL,expires_at_ms=NULL", ()),
                     D1Statement("INSERT INTO contract_barriers VALUES('global',1)", ())))
    with pytest.raises(StateConflict):
        upgrade_mock_d1_workspace(directory, intent)
    assert not (directory / 'maintenance-lock.json').exists()
    assert session.execute((D1Statement('SELECT revision,epoch,state FROM coordinator_schema_authority', ()),))[0]['results'] == [{'revision': 0, 'epoch': 1, 'state': 'ready'}]
    session.execute((D1Statement('UPDATE contract_barriers SET is_blocked=0', ()),))
    receipt = upgrade_mock_d1_workspace(directory, intent)
    active = LocalD1VersionedSession(directory, plan, 1, 2)
    active.execute((D1Statement("UPDATE compute_jobs SET state='running',owner='new-worker',expires_at_ms=999999", ()),))
    assert upgrade_mock_d1_workspace(directory, intent) == receipt
    assert active.execute((D1Statement('SELECT state,owner FROM compute_jobs', ()),))[0]['results'] == [{'state': 'running', 'owner': 'new-worker'}]


@native
def test_actual_changed_history_plan_with_same_target_schema_is_rejected_before_journal(tmp_path):
    plan = definition()
    directory = tmp_path / 'd1'
    create(directory, plan)
    intent = MaintenanceIntent(plan, 0, 1)
    upgrade_mock_d1_workspace(directory, intent)
    changed_initial = D1Schema(tuple(sql.replace('CREATE TABLE ', 'CREATE TABLE IF NOT EXISTS ', 1) for sql in plan.schemas[0].statements))
    changed_plan = AdditiveMigrationPlan((changed_initial, *plan.schemas[1:]))
    changed = MaintenanceIntent(changed_plan, 0, 1)
    assert changed.target_schema_id == intent.target_schema_id
    marker = (directory / 'mock-d1.json').read_bytes()
    with pytest.raises(StateConflict, match='history'):
        upgrade_mock_d1_workspace(directory, changed)
    assert not (directory / 'maintenance-lock.json').exists()
    assert (directory / 'mock-d1.json').read_bytes() == marker
    with pytest.raises(ValueError):
        LocalD1VersionedSession(directory, changed_plan, 2, 2)


@pytest.mark.parametrize('value', [True, 0])
def test_unproven_guard_rows_do_not_authorize_a_migration_result(monkeypatch, value):
    from adapters import mock_d1_upgrade
    from coordination.d1_migrations import LEDGER
    body = {'is_mock': True, 'network_providers': [], 'are_sdk_children_closed': True,
            'is_environment_sanitized': True, 'operations': [{'outcome': 'completed', 'results': [
                {'success': True, 'results': [{'schema_ok': value}]}]}]}
    monkeypatch.setattr(mock_d1_upgrade, 'run_d1_maintenance_batch', lambda *args: {
        'outcome': 'completed', 'is_child_stopped': True, 'stdout': json.dumps(body)})
    with pytest.raises(StateConflict):
        mock_d1_upgrade._invoke(None, (LEDGER.guard(),))


@native
def test_actual_competing_upgrade_calls_return_one_completed_epoch(tmp_path):
    intent = MaintenanceIntent(definition(), 0, 1)
    directory = tmp_path / 'd1'
    create(directory, intent.plan)
    barrier = threading.Barrier(2)
    def upgrade():
        barrier.wait(timeout=5)
        return upgrade_mock_d1_workspace(directory, intent)
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = [future.result() for future in (executor.submit(upgrade), executor.submit(upgrade))]
    assert outcomes[0] == outcomes[1] and outcomes[0]['epoch'] == 2
    session = LocalD1VersionedSession(directory, intent.plan, 2, 2)
    assert session.execute((D1Statement('SELECT COUNT(*) AS count FROM coordinator_schema_history', ()),))[0]['results'] == [{'count': 3}]


@native
def test_actual_restore_clone_cannot_use_upgrade_to_unfreeze_or_start_sdk(tmp_path, monkeypatch):
    from adapters import mock_d1_upgrade
    intent = MaintenanceIntent(definition(), 0, 1)
    directory = tmp_path / 'd1'
    create(directory, intent.plan)
    archive = tmp_path / 'backup.zip'
    backup = create_mock_d1_backup(directory, archive, intent.source_schema_id)
    clone = tmp_path / 'clone'
    restore_mock_d1_backup(archive, clone, backup['backup_id'], intent.source_schema_id)
    def forbidden(*args, **kwargs):
        pytest.fail('restored clone started migration SDK')
    monkeypatch.setattr(mock_d1_upgrade, 'run_d1_maintenance_batch', forbidden)
    with pytest.raises(ValueError, match='restore'):
        upgrade_mock_d1_workspace(clone, intent)
    assert (clone / 'restore-lock.json').is_file()


@native
def test_actual_sdk_deadline_leaves_fence_and_resumes_from_d1(tmp_path, monkeypatch):
    from adapters import mock_d1_upgrade
    intent = MaintenanceIntent(definition(), 0, 1)
    directory = tmp_path / 'd1'
    create(directory, intent.plan)
    original = mock_d1_upgrade.run_d1_maintenance_batch
    observed = []
    def deadline(statements, capability):
        if statements == apply_revision(intent.plan, 1, 2):
            receipt = original(statements, capability, seconds=.05)
            observed.append(receipt)
            return receipt
        return original(statements, capability)
    with monkeypatch.context() as patch:
        patch.setattr(mock_d1_upgrade, 'run_d1_maintenance_batch', deadline)
        with pytest.raises(StateConflict):
            upgrade_mock_d1_workspace(directory, intent)
    assert observed[0]['outcome'] == 'deadline_terminated' and observed[0]['is_child_stopped'] is True
    assert (directory / 'maintenance-lock.json').is_file()
    receipt = upgrade_mock_d1_workspace(directory, intent)
    assert receipt['epoch'] == 2
    assert not (directory / 'maintenance-lock.json').exists()
