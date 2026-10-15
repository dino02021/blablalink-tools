"""Python object port over actual, marked local Miniflare D1 invocations."""

import json

from adapters.mock_d1_workspace import create_mock_d1_workspace, require_mock_d1_workspace
from coordination.d1_binary import D1Binary
from coordination.d1_objects import (SCHEMA_ID, SCHEMA_STATEMENTS, begin_transport, list_chunks,
    put_chunk, read_chunk, read_object_head, read_transport, restore_manifest, seal_verified_transport, transport_id, validate_schema)
from domain.chunks import decode_value, verify_chunk
from domain.snapshots import canonical_json
from executors.local.emulator_process import run_d1_emulator


class LocalD1Objects:
    def __init__(self, directory, *, is_create=False):
        if type(is_create) is not bool:
            raise ValueError('explicit mock D1 creation mode required')
        self.directory = (create_mock_d1_workspace(directory, SCHEMA_ID) if is_create
                          else require_mock_d1_workspace(directory, SCHEMA_ID))
        self._execute((), is_initialize=True)

    def begin(self, manifest):
        identity = transport_id(manifest)
        results = self._execute((begin_transport(manifest), read_transport(identity)))
        if len(results[1]) != 1 or restore_manifest(results[1][0]) != manifest:
            raise ValueError('D1 transport admission or identity rejected')
        return identity

    def missing(self, manifest):
        rows = self._execute((read_transport(transport_id(manifest)), list_chunks(transport_id(manifest))))
        if len(rows[0]) != 1 or restore_manifest(rows[0][0]) != manifest:
            raise ValueError('D1 transport not begun')
        seen = set()
        for row in rows[1]:
            ordinal = row['ordinal']
            if type(ordinal) is not int or not 0 <= ordinal < len(manifest.chunk_hashes) or ordinal in seen:
                raise ValueError('invalid stored D1 chunk inventory')
            expected_size = min(32768, manifest.compressed_size - ordinal * 32768)
            if row['chunk_hash'] != manifest.chunk_hashes[ordinal] or row['size'] != expected_size:
                raise ValueError('stored D1 chunk metadata differs')
            seen.add(ordinal)
        return tuple(index for index in range(len(manifest.chunk_hashes)) if index not in seen)

    def put(self, manifest, ordinal, data):
        statement = put_chunk(manifest, ordinal, data)
        rows = self._execute((statement, read_chunk(transport_id(manifest), ordinal)))[1]
        if len(rows) != 1 or self._restore_chunk(manifest, ordinal, rows[0]) != data:
            raise ValueError('D1 immutable chunk differs')

    def seal(self, manifest):
        if self.missing(manifest):
            raise ValueError('D1 transport incomplete')
        value = self._read_value(manifest)
        self._execute((*seal_verified_transport(manifest),))
        rows = self._execute((read_transport(transport_id(manifest)),))[0]
        if len(rows) != 1 or restore_manifest(rows[0]) != manifest or rows[0]['is_sealed'] != 1:
            raise ValueError('D1 seal unproven')
        return value

    def read(self, object_id):
        rows = self._execute((read_object_head(object_id),))[0]
        if len(rows) != 1:
            raise ValueError('sealed D1 object unavailable')
        return self._read_value(restore_manifest(rows[0]))

    def _read_value(self, manifest):
        chunks = []
        for ordinal in range(len(manifest.chunk_hashes)):
            rows = self._execute((read_chunk(transport_id(manifest), ordinal),))[0]
            if len(rows) != 1:
                raise ValueError('D1 chunk missing')
            chunks.append(self._restore_chunk(manifest, ordinal, rows[0]))
        return decode_value(manifest, tuple(chunks))

    def _execute(self, statements, *, is_initialize=False):
        payload = {'schema_statements': list(SCHEMA_STATEMENTS) if is_initialize else [], 'operations': []}
        guarded_statements = (validate_schema(), *statements)
        payload['operations'] = [{'kind': 'batch', 'statements': [statement.wire for statement in guarded_statements]}]
        if len(canonical_json(payload).encode()) > 65536:
            raise ValueError('D1 invocation control payload exceeds 64 KiB')
        receipt = run_d1_emulator(payload, mock_directory=self.directory, schema_id=SCHEMA_ID)
        if receipt['outcome'] != 'completed' or not receipt['is_child_stopped']:
            raise ValueError('local D1 invocation outcome unproven')
        result = json.loads(receipt['stdout'])
        if not result.get('are_sdk_children_closed') or result.get('outcome') == 'emulator_failed':
            raise ValueError('local D1 runtime failed')
        operation = result['operations'][0]
        if operation['outcome'] != 'completed' or len(operation['results']) != len(guarded_statements):
            raise ValueError('local D1 batch rejected')
        if any(item.get('success') is not True for item in operation['results']):
            raise ValueError('local D1 statement rejected')
        if operation['results'][0]['results'] != [{'schema_ok': 1}]:
            raise ValueError('local D1 schema unproven')
        return [item['results'] for item in operation['results'][1:]]

    @staticmethod
    def _restore_chunk(manifest, ordinal, row):
        if set(row) != {'ordinal', 'chunk_hash', 'chunk_data'} or row['ordinal'] != ordinal or row['chunk_hash'] != manifest.chunk_hashes[ordinal]:
            raise ValueError('D1 chunk row differs')
        data = D1Binary.from_wire(row['chunk_data']).data
        verify_chunk(manifest, ordinal, data)
        return data
