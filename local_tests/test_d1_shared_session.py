import json
import sqlite3
import sys

import pytest

from adapters.local_d1_objects import LocalD1Objects
from adapters.local_d1_session import LocalD1Session
from coordination.d1_objects import SCHEMA_STATEMENTS as OBJECT_SCHEMA, SCHEMA_ID as OBJECT_SCHEMA_ID
from coordination.receipt_d1_contract import SCHEMA_STATEMENTS as HEAD_SCHEMA, build_receipt_cas_batch, verify_receipt_cas_result
from coordination.d1_cas_contract import HeadCasIntent
from coordination.d1_schema import D1Schema
from coordination.d1_statement import D1Statement
from domain.chunks import encode_value
from executors.local.emulator_process import run_d1_emulator


def schema():
    return D1Schema((*OBJECT_SCHEMA, *HEAD_SCHEMA))


def test_schema_identity_preserves_existing_object_workspace_and_atomic_bootstrap():
    assert D1Schema(OBJECT_SCHEMA).schema_id == OBJECT_SCHEMA_ID
    definition = schema()
    with sqlite3.connect(':memory:') as connection:
        for declaration in definition.bootstrap:
            if isinstance(declaration, str): connection.execute(declaration)
            else: connection.execute(declaration['sql'], declaration['parameters'])
        guard = definition.guard()
        assert connection.execute(guard.sql, guard.parameters).fetchone() == (1,)
        connection.execute('CREATE TRIGGER unknown AFTER INSERT ON contract_heads BEGIN SELECT 1; END')
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute(guard.sql, guard.parameters)


@pytest.mark.parametrize('statements', [(), ('DELETE FROM jobs',), ('CREATE VIEW unknown AS SELECT 1',),
    ('CREATE TABLE same(id TEXT)', 'CREATE TABLE same(id TEXT)')])
def test_schema_rejects_unknown_or_ambiguous_declarations(statements):
    with pytest.raises(ValueError):
        D1Schema(statements)


@pytest.mark.parametrize('value', [True, 2 ** 53, -(2 ** 53), 1.5, {}, b'bytes'])
def test_shared_statement_rejects_inexact_or_unsupported_wire_values(value):
    with pytest.raises(ValueError):
        D1Statement('SELECT ?', (value,))


def test_shared_statement_copies_caller_parameter_sequence():
    parameters = ['original']
    statement = D1Statement('SELECT ?', parameters)
    parameters.append('later')
    assert statement.parameters == ('original',)


@pytest.mark.skipif(sys.platform != 'win32', reason='requires verified Windows SDK process supervision')
def test_actual_shared_database_keeps_sealed_objects_and_atomic_heads_together(tmp_path):
    definition = schema()
    directory = tmp_path / 'd1'
    session = LocalD1Session(directory, definition, is_create=True)
    objects = LocalD1Objects.with_session(session)
    value = {'scope_id': 'mock_union', 'training': [{'member': 'mock', 'value': 0}]}
    manifest, chunks = encode_value(value)
    objects.begin(manifest)
    objects.put_many(manifest, chunks)
    assert objects.seal(manifest) == value
    intent = HeadCasIntent('shared-request', 'mock_union', 'training', 0, manifest.object_id, 'mock-release', 'mock-actor')
    guard = D1Statement("SELECT CASE WHEN EXISTS(SELECT 1 FROM object_heads WHERE object_id=?) THEN 1 ELSE json('unsealed_object') END", (manifest.object_id,))
    result = session.execute((guard, *build_receipt_cas_batch(intent)))
    assert verify_receipt_cas_result(intent, result[1:]) == intent.receipt
    reopened = LocalD1Session(directory, definition)
    assert LocalD1Objects.with_session(reopened).read(manifest.object_id) == value
    result = reopened.execute(build_receipt_cas_batch(intent))
    assert verify_receipt_cas_result(intent, result) == intent.receipt
    frames = reopened.execute_frames(((D1Statement('SELECT COUNT(*) AS count FROM object_heads', ()),),
                                      (D1Statement('SELECT revision FROM contract_heads', ()),)))
    assert frames[0][0]['results'] == [{'count': 1}]
    assert frames[1][0]['results'] == [{'revision': 1}]
    with pytest.raises(ValueError):
        LocalD1Session(directory, D1Schema(OBJECT_SCHEMA))


@pytest.mark.skipif(sys.platform != 'win32', reason='requires verified Windows SDK process supervision')
def test_existing_partial_schema_is_rejected_without_silently_recreating_missing_table(tmp_path):
    definition = schema()
    directory = tmp_path / 'd1'
    session = LocalD1Session(directory, definition, is_create=True)
    session.execute((D1Statement('DROP TABLE object_chunks', ()),))
    with pytest.raises(ValueError):
        LocalD1Session(directory, definition)
    receipt = run_d1_emulator({'schema_statements': [], 'operations': [{
        'kind': 'query', 'sql': "SELECT COUNT(*) AS count FROM sqlite_master WHERE name='object_chunks'", 'parameters': [],
    }]}, mock_directory=directory, schema_id=definition.schema_id)
    assert json.loads(receipt['stdout'])['operations'][0]['result']['results'] == [{'count': 0}]
