import json
import sys

import pytest

from adapters.mock_d1_workspace import create_mock_d1_workspace, require_mock_d1_workspace
from executors.local.emulator_process import run_d1_emulator


def test_mock_storage_requires_exact_marker_new_directory_and_no_restore_fence(tmp_path):
    directory = tmp_path / 'mock'
    assert create_mock_d1_workspace(directory, 'a' * 64) == directory.resolve()
    with pytest.raises(ValueError):
        create_mock_d1_workspace(directory, 'a' * 64)
    with pytest.raises(ValueError):
        require_mock_d1_workspace(directory, 'b' * 64)
    (directory / 'restore-lock.json').write_text('{}')
    with pytest.raises(ValueError):
        require_mock_d1_workspace(directory, 'a' * 64)


@pytest.mark.skipif(sys.platform != 'win32', reason='requires verified Windows SDK process supervision')
def test_actual_d1_survives_two_owned_sdk_processes_and_atomic_schema_failure(tmp_path):
    directory = create_mock_d1_workspace(tmp_path / 'd1', 'a' * 64)
    schema = ['CREATE TABLE IF NOT EXISTS snapshots(id TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID']
    first = run_d1_emulator({'schema_statements': schema, 'operations': [
        {'kind': 'query', 'sql': 'INSERT INTO snapshots VALUES(?,?)', 'parameters': ['mock-object', 'immutable']},
    ]}, mock_directory=directory, schema_id='a' * 64)
    assert first['outcome'] == 'completed'
    assert json.loads(first['stdout'])['operations'][0]['outcome'] == 'completed'
    failed = run_d1_emulator({'schema_statements': [
        'CREATE TABLE partial_schema(id TEXT)', 'INSERT INTO missing_table VALUES(1)',
    ], 'operations': []}, mock_directory=directory, schema_id='a' * 64)
    assert json.loads(failed['stdout'])['outcome'] == 'emulator_failed'
    second = run_d1_emulator({'schema_statements': schema, 'operations': [
        {'kind': 'query', 'sql': 'SELECT * FROM snapshots', 'parameters': []},
        {'kind': 'query', 'sql': "SELECT COUNT(*) AS count FROM sqlite_master WHERE name='partial_schema'", 'parameters': []},
    ]}, mock_directory=directory, schema_id='a' * 64)
    observed = json.loads(second['stdout'])
    assert second['outcome'] == 'completed' and observed['are_sdk_children_closed']
    assert observed['operations'][0]['result']['results'] == [{'id': 'mock-object', 'value': 'immutable'}]
    assert observed['operations'][1]['result']['results'] == [{'count': 0}]
