import json
import socket
import sys
import subprocess
from pathlib import Path

import pytest

from executors.local.emulator_process import run_d1_emulator
from adapters.mock_d1_workspace import create_mock_d1_workspace


def available_blocked_port():
    for port in (6000, 6665, 6666, 6667, 6697, 10080):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            try:
                server.bind(('127.0.0.1', port))
                return port
            except OSError:
                pass
    raise AssertionError('available local blocked-port fixture required')


@pytest.mark.skipif(sys.platform != 'win32', reason='requires verified Windows SDK process supervision')
def test_forbidden_os_port_is_reallocated_before_any_d1_mutation():
    forbidden = available_blocked_port()
    receipt = run_d1_emulator({'mock_entry_ports': [forbidden, 0],
        'schema_statements': ['CREATE TABLE counter(value INTEGER NOT NULL)'], 'operations': [
            {'kind': 'query', 'sql': 'INSERT INTO counter VALUES(1)', 'parameters': []},
            {'kind': 'query', 'sql': 'SELECT COUNT(*) AS count FROM counter', 'parameters': []},
        ]})
    result = json.loads(receipt['stdout'])
    assert receipt['outcome'] == 'completed'
    assert result.get('outcome') != 'emulator_failed', result
    assert result['loopback_allocation']['rejected_ports'][0] == forbidden
    assert 2 <= result['loopback_allocation']['attempts'] <= 8
    assert result['operations'][1]['result']['results'] == [{'count': 1}]
    assert result['are_sdk_children_closed']


def test_port_contract_limits_and_non_port_errors_do_not_retry():
    script = """
import assert from 'node:assert/strict';
import {FETCH_BLOCKED_PORTS,isFetchCompatibleLoopback,createLoopbackRuntime} from './coordination/offline_d1/loopback_runtime.mjs';
for(const port of FETCH_BLOCKED_PORTS)assert.equal(isFetchCompatibleLoopback(new URL(`http://127.0.0.1:${port}`)),false);
for(const port of [80,1024,8787,49152,65535])assert.equal(isFetchCompatibleLoopback(new URL(`http://127.0.0.1:${port}`)),true);
assert.equal(isFetchCompatibleLoopback(new URL('http://192.0.2.1:8787')),false);
let created=0,disposed=0;
class Blocked {constructor(){created++;this.ready=Promise.resolve(new URL('http://127.0.0.1:6000'));}async dispose(){disposed++;}}
await assert.rejects(()=>createLoopbackRuntime(Blocked,{}),error=>error.code==='LOCAL_FETCH_PORT_EXHAUSTED');
assert.equal(created,8);assert.equal(disposed,8);
created=0;disposed=0;
class Broken {constructor(){created++;this.ready=Promise.reject(new Error('injected unrelated boot failure'));}async dispose(){disposed++;}}
await assert.rejects(()=>createLoopbackRuntime(Broken,{}),/injected unrelated boot failure/);
assert.equal(created,1);assert.equal(disposed,1);
created=0;disposed=0;
class Remote {constructor(){created++;this.ready=Promise.resolve(new URL('http://192.0.2.1:8787'));}async dispose(){disposed++;}}
await assert.rejects(()=>createLoopbackRuntime(Remote,{}),/unexpected local SDK origin/);
assert.equal(created,1);assert.equal(disposed,1);
"""
    result = subprocess.run(['node', '--input-type=module', '-e', script], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(sys.platform != 'win32', reason='requires verified Windows SDK process supervision')
def test_port_exhaustion_never_runs_schema_or_task_sql_and_reaps_every_sdk_child(tmp_path):
    forbidden = available_blocked_port()
    directory = create_mock_d1_workspace(tmp_path / 'd1', 'a' * 64)
    receipt = run_d1_emulator({'mock_entry_ports': [forbidden] * 8,
        'schema_statements': ['CREATE TABLE must_not_run(value INTEGER)'],
        'operations': [{'kind': 'query', 'sql': 'INSERT INTO must_not_run VALUES(1)', 'parameters': []}]},
        mock_directory=directory, schema_id='a' * 64)
    result = json.loads(receipt['stdout'])
    assert receipt['outcome'] == 'completed' and result['outcome'] == 'emulator_failed'
    assert result['error_cause']['code'] == 'LOCAL_FETCH_PORT_EXHAUSTED'
    assert result['are_sdk_children_closed'] and len(result['sdk_child_results']) == 8
    probe = run_d1_emulator({'schema_statements': [], 'operations': [
        {'kind': 'query', 'sql': "SELECT COUNT(*) AS count FROM sqlite_master WHERE name='must_not_run'", 'parameters': []}]},
        mock_directory=directory, schema_id='a' * 64)
    assert json.loads(probe['stdout'])['operations'][0]['result']['results'] == [{'count': 0}]
