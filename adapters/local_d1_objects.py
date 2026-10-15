"""Python object port over actual, marked local Miniflare D1 invocations."""

import json

from adapters.mock_d1_workspace import create_mock_d1_workspace, require_mock_d1_workspace
from coordination.d1_binary import D1Binary
from coordination.d1_objects import (SCHEMA_ID, SCHEMA_STATEMENTS, begin_transport, list_chunks,
    put_chunk, read_chunk, read_object_head, read_transport, restore_manifest, seal_verified_transport, transport_id, validate_schema)
from domain.chunks import decode_value, verify_chunk
from domain.snapshots import canonical_json
from executors.local.emulator_process import run_d1_emulator, run_d1_object_session


class LocalD1Objects:
    @classmethod
    def with_session(cls, session):
        from adapters.local_d1_session import LocalD1Session
        from coordination.d1_schema import D1Schema
        if not isinstance(session, LocalD1Session) or not set(D1Schema(SCHEMA_STATEMENTS).objects).issubset(set(session.schema.objects)):
            raise ValueError('shared D1 session must own the complete object schema')
        instance = cls.__new__(cls)
        instance.directory = session.directory
        instance._session = session
        return instance

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
        page = rows[1]
        after = -1
        while True:
            if len(page) > 64:
                raise ValueError('D1 chunk inventory page exceeds bound')
            for row in page:
                if set(row) != {'ordinal', 'chunk_hash', 'size'}:
                    raise ValueError('invalid D1 chunk inventory row')
                ordinal = row['ordinal']
                if type(ordinal) is not int or not after < ordinal < len(manifest.chunk_hashes) or ordinal in seen:
                    raise ValueError('invalid stored D1 chunk inventory')
                expected_size = min(32768, manifest.compressed_size - ordinal * 32768)
                if row['chunk_hash'] != manifest.chunk_hashes[ordinal] or type(row['size']) is not int or row['size'] != expected_size:
                    raise ValueError('stored D1 chunk metadata differs')
                seen.add(ordinal)
                after = ordinal
            if len(page) < 64 or after == len(manifest.chunk_hashes) - 1:
                break
            page = self._execute((list_chunks(transport_id(manifest), after),))[0]
        return tuple(index for index in range(len(manifest.chunk_hashes)) if index not in seen)

    def put(self, manifest, ordinal, data):
        statement = put_chunk(manifest, ordinal, data)
        rows = self._execute((statement, read_chunk(transport_id(manifest), ordinal)))[1]
        if len(rows) != 1 or self._restore_chunk(manifest, ordinal, rows[0]) != data:
            raise ValueError('D1 immutable chunk differs')

    def put_many(self, manifest, chunks):
        if type(chunks) is not tuple or len(chunks) != len(manifest.chunk_hashes):
            raise ValueError('complete bounded chunk tuple required')
        frames = tuple((put_chunk(manifest, ordinal, data), read_chunk(transport_id(manifest), ordinal))
                       for ordinal, data in enumerate(chunks))
        results = self._execute_frames(frames)
        for ordinal, (frame, data) in enumerate(zip(results, chunks)):
            if len(frame[1]) != 1 or self._restore_chunk(manifest, ordinal, frame[1][0]) != data:
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
        frames = tuple((read_chunk(transport_id(manifest), ordinal),) for ordinal in range(len(manifest.chunk_hashes)))
        results = self._execute_frames(frames)
        for ordinal, frame in enumerate(results):
            rows = frame[0]
            if len(rows) != 1:
                raise ValueError('D1 chunk missing')
            chunks.append(self._restore_chunk(manifest, ordinal, rows[0]))
        return decode_value(manifest, tuple(chunks))

    def _execute_frames(self, frames):
        if hasattr(self, '_session'):
            return [[item['results'] for item in result] for result in self._session.execute_frames(frames)]
        # Keep SDK bridge/compiler allocations bounded as well as wire bytes.
        # Large conversations exhausted the measured 512 MiB SDK Job budget.
        if len(frames) > 32:
            results = []
            for offset in range(0, len(frames), 32):
                results.extend(self._execute_frames(frames[offset:offset + 32]))
            return results
        if len(frames) == 1:
            return [self._execute(frames[0])]
        payload = {'schema_statements': [], 'operations': [
            {'kind': 'batch', 'statements': [statement.wire for statement in (validate_schema(), *frame)]}
            for frame in frames]}
        receipt = run_d1_object_session(payload, mock_directory=self.directory, schema_id=SCHEMA_ID)
        if receipt['outcome'] != 'completed' or not receipt['is_child_stopped']:
            raise ValueError(f"D1 object session outcome unproven: {receipt['outcome']}, exit={receipt['exit_code']}: {receipt.get('sdk_diagnostic','')}")
        result = receipt['object_reply']
        if result.get('are_sdk_children_closed') is not True or result.get('outcome') == 'emulator_failed':
            raise ValueError('D1 object session failed')
        if len(result['operations']) != len(frames):
            raise ValueError('D1 object frame count differs')
        return [self._verify_operation(operation, len(frame)) for operation, frame in zip(result['operations'], frames)]

    def _execute(self, statements, *, is_initialize=False):
        if hasattr(self, '_session'):
            return [item['results'] for item in self._session.execute(statements)]
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
        return self._verify_operation(result['operations'][0], len(statements))

    @staticmethod
    def _verify_operation(operation, statement_count):
        if operation['outcome'] != 'completed' or len(operation['results']) != statement_count + 1:
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
