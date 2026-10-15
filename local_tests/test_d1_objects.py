import json
import sqlite3
import sys

import pytest

from adapters.local_d1_objects import LocalD1Objects
from coordination.d1_binary import D1Binary
from coordination.d1_objects import SCHEMA_STATEMENTS, ObjectStatement, begin_transport, put_chunk, transport_id, validate_schema, SCHEMA_ID
from domain.chunks import encode_value
from executors.local.emulator_process import run_d1_emulator


def test_d1_transport_identity_and_chunks_are_immutable_in_statement_contract():
    manifest, chunks = encode_value({'mock': 'value'})
    with sqlite3.connect(':memory:') as connection:
        for sql in SCHEMA_STATEMENTS:
            connection.execute(sql)
        for statement in (begin_transport(manifest), begin_transport(manifest), put_chunk(manifest, 0, chunks[0]), put_chunk(manifest, 0, chunks[0])):
            connection.execute(statement.sql, tuple(value.data if isinstance(value, D1Binary) else value for value in statement.parameters))
        assert connection.execute('SELECT COUNT(*) FROM object_transports').fetchone() == (1,)
        assert connection.execute('SELECT chunk_data FROM object_chunks').fetchone() == (chunks[0],)
        assert len(json.dumps(put_chunk(manifest, 0, chunks[0]).wire).encode()) < 65536
        assert transport_id(manifest) != manifest.object_id


def test_object_control_frame_refuses_oversize_before_invocation():
    with pytest.raises(ValueError):
        ObjectStatement('SELECT ?', ('x' * 65536,))


def test_schema_guard_rejects_extra_trigger_before_writing():
    with sqlite3.connect(':memory:') as connection:
        for sql in SCHEMA_STATEMENTS:
            connection.execute(sql)
        guard = validate_schema()
        assert connection.execute(guard.sql, guard.parameters).fetchone() == (1,)
        connection.execute('CREATE TRIGGER unexpected AFTER INSERT ON object_chunks BEGIN SELECT 1; END')
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute(guard.sql, guard.parameters)


@pytest.mark.skipif(sys.platform != 'win32', reason='requires verified Windows SDK process supervision')
def test_actual_d1_object_resume_seal_and_read_across_owned_processes(tmp_path):
    manifest, chunks = encode_value({'mock_members': [{'identity': ['area', 'member'], 'value': 0}]})
    directory = tmp_path / 'd1'
    store = LocalD1Objects(directory, is_create=True)
    assert store.begin(manifest) == transport_id(manifest)
    assert store.missing(manifest) == (0,)
    with pytest.raises(ValueError):
        store.read(manifest.object_id)
    with pytest.raises(ValueError):
        store.seal(manifest)
    store.put(manifest, 0, chunks[0])
    restarted = LocalD1Objects(directory)
    assert restarted.missing(manifest) == ()
    restarted.put(manifest, 0, chunks[0])
    value = restarted.seal(manifest)
    assert value['mock_members'][0]['value'] == 0
    assert restarted.read(manifest.object_id) == value
    restarted.put(manifest, 0, chunks[0])
    failure = run_d1_emulator({'schema_statements': [], 'operations': [
        {'kind': 'query', 'sql': 'CREATE TRIGGER unexpected AFTER INSERT ON object_chunks BEGIN SELECT 1; END', 'parameters': []},
    ]}, mock_directory=directory, schema_id=SCHEMA_ID)
    assert json.loads(failure['stdout'])['operations'][0]['outcome'] == 'completed'
    with pytest.raises(ValueError):
        restarted.begin(manifest)
