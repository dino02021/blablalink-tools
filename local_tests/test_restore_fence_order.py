import json
from pathlib import Path
import sqlite3

import pytest

from executors.local.demo import run_demo, workspace_status
from executors.local.isolated import run_isolated_worker
from executors.local.process_guard import run_python_module
from executors.local.worker import run_worker


@pytest.fixture
def pending_workspace(tmp_path):
    directory = tmp_path/'mock'
    summary = run_demo(directory)
    with sqlite3.connect(directory/'state.sqlite') as connection:
        connection.executescript('''
            DROP TABLE healthcheck_workflow_state;
            DROP TABLE healthcheck_workflow_policy_requests;
            DROP TABLE healthcheck_workflow_policy_receipts;
            DROP TABLE healthcheck_workflow_heads;
            DELETE FROM schema_migrations WHERE version=14;
        ''')
    return directory,summary


@pytest.mark.parametrize('entry', ['worker','isolated','status'])
def test_restore_fence_prevents_pending_migration_before_any_writer_or_child(pending_workspace, entry, monkeypatch):
    directory,summary = pending_workspace
    (directory/'restore-lock.json').write_text('{}',encoding='utf-8')
    before = (directory/'state.sqlite').read_bytes()
    monkeypatch.setattr('executors.local.isolated.run_python_module',unexpected_entry)
    monkeypatch.setattr('executors.local.worker.LocalWorkerStore',unexpected_entry)
    if entry=='status':
        result = workspace_status(directory)
        assert result['jobs'][0]['job_id']==summary['job_id']
    else:
        with pytest.raises(ValueError,match='fenced'):
            (run_worker if entry=='worker' else run_isolated_worker)(directory,'mock-release-v1')
    assert (directory/'state.sqlite').read_bytes()==before
    with sqlite3.connect(directory/'state.sqlite') as connection:
        assert connection.execute('SELECT COUNT(*) FROM schema_migrations WHERE version=14').fetchone()[0]==0
        assert connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='healthcheck_workflow_heads'").fetchone()[0]==0


def test_legacy_status_does_not_migrate_even_without_restore_fence(pending_workspace):
    directory,summary = pending_workspace
    before = (directory/'state.sqlite').read_bytes()
    result = workspace_status(directory)
    assert result['jobs']==[{'job_id':summary['job_id'],'state':'succeeded','error_code':None}]
    assert (directory/'state.sqlite').read_bytes()==before


def test_actual_status_cli_on_fenced_pending_schema_is_read_only(pending_workspace):
    directory,summary = pending_workspace
    (directory/'restore-lock.json').write_text('{}',encoding='utf-8')
    before = (directory/'state.sqlite').read_bytes()
    receipt = run_python_module('executors.local',('status','--workspace',str(directory)),seconds=30)
    assert receipt['outcome']=='completed'
    assert json.loads(receipt['stdout'])['jobs'][0]['job_id']==summary['job_id']
    assert (directory/'state.sqlite').read_bytes()==before


def test_fence_is_checked_before_workspace_validation(pending_workspace, monkeypatch):
    directory,_ = pending_workspace
    (directory/'restore-lock.json').write_text('{}',encoding='utf-8')
    monkeypatch.setattr('adapters.mock_workspace._is_mock_marker',unexpected_entry)
    with pytest.raises(ValueError,match='fenced'): run_worker(directory,'code')
    with pytest.raises(ValueError,match='fenced'): run_isolated_worker(directory,'code')


def test_missing_schema_status_fails_without_creating_tables(tmp_path):
    from domain.mock_workspace import MOCK_WORKSPACE_MARKER

    (tmp_path/'mock-workspace.json').write_text(json.dumps(MOCK_WORKSPACE_MARKER),encoding='utf-8')
    with sqlite3.connect(tmp_path/'state.sqlite') as connection:
        connection.execute('CREATE TABLE unrelated(value TEXT)')
    before = (tmp_path/'state.sqlite').read_bytes()
    with pytest.raises(sqlite3.Error): workspace_status(tmp_path)
    assert (tmp_path/'state.sqlite').read_bytes()==before


def unexpected_entry(*args,**kwargs):
    raise AssertionError('fenced workspace must not initialize writer or child')
