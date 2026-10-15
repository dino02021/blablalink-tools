import json
import sqlite3
import sys
import hashlib
import random
import zlib

import pytest

from adapters.local_d1_objects import LocalD1Objects
from coordination.d1_binary import D1Binary
from coordination.d1_objects import SCHEMA_STATEMENTS, ObjectStatement, begin_transport, put_chunk, transport_id, validate_schema, SCHEMA_ID, seal_verified_transport
from domain.chunks import encode_value, decode_value, ChunkManifest
from domain.snapshots import canonical_json
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


def test_alternative_valid_encodings_share_object_identity_and_admission_is_bounded():
    value = random.Random(108).randbytes(5000).hex() + 'repeated' * 1000
    raw = canonical_json(value).encode()
    object_id = hashlib.sha256(raw).hexdigest()
    variants = {}
    for level in range(1, 10):
        for strategy in (zlib.Z_DEFAULT_STRATEGY, zlib.Z_FILTERED, zlib.Z_HUFFMAN_ONLY, zlib.Z_RLE, zlib.Z_FIXED):
            compressor = zlib.compressobj(level, strategy=strategy)
            data = compressor.compress(raw) + compressor.flush()
            manifest = ChunkManifest(object_id, object_id, len(raw), len(data), (hashlib.sha256(data).hexdigest(),))
            variants[transport_id(manifest)] = (manifest, data)
    assert len(variants) >= 5
    with sqlite3.connect(':memory:') as connection:
        for sql in SCHEMA_STATEMENTS:
            connection.execute(sql)
        first = None
        for ordinal, (manifest, data) in enumerate(list(variants.values())[:5]):
            statement = begin_transport(manifest)
            connection.execute(statement.sql, statement.parameters)
            if ordinal == 4:
                assert connection.execute('SELECT COUNT(*) FROM object_transports').fetchone() == (4,)
                continue
            assert decode_value(manifest, (data,)) == value
            statement = put_chunk(manifest, 0, data)
            connection.execute(statement.sql, tuple(binding.data if isinstance(binding, D1Binary) else binding for binding in statement.parameters))
            for statement in seal_verified_transport(manifest):
                connection.execute(statement.sql, statement.parameters)
            first = first or transport_id(manifest)
        assert connection.execute('SELECT object_id,transport_id FROM object_heads').fetchall() == [(object_id, first)]


@pytest.mark.skipif(sys.platform != 'win32', reason='requires verified Windows SDK process supervision')
def test_stored_chunk_corruption_cannot_be_promoted_to_sealed_input(tmp_path):
    manifest, chunks = encode_value({'mock': 'corruption fixture'})
    store = LocalD1Objects(tmp_path / 'd1', is_create=True)
    store.begin(manifest)
    store.put(manifest, 0, chunks[0])
    corrupt = bytes([chunks[0][0] ^ 1]) + chunks[0][1:]
    receipt = run_d1_emulator({'schema_statements': [], 'operations': [{
        'kind': 'query', 'sql': 'UPDATE object_chunks SET chunk_data=? WHERE transport_id=? AND ordinal=0',
        'parameters': [D1Binary(corrupt).wire, transport_id(manifest)],
    }]}, mock_directory=store.directory, schema_id=SCHEMA_ID)
    assert json.loads(receipt['stdout'])['operations'][0]['outcome'] == 'completed'
    assert store.missing(manifest) == ()  # Metadata alone cannot prove content.
    with pytest.raises(ValueError):
        store.seal(manifest)
    with pytest.raises(ValueError):
        store.read(manifest.object_id)


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
