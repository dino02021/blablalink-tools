"""Shared local D1 binding session; modules use one authoritative mock database."""

import json

from adapters.mock_d1_workspace import create_mock_d1_workspace, require_mock_d1_workspace
from coordination.d1_schema import D1Schema
from coordination.d1_statement import D1Statement
from domain.snapshots import canonical_json
from executors.local.emulator_process import run_d1_emulator, run_d1_object_session


class LocalD1Session:
    def __init__(self, directory, schema, *, is_create=False):
        if not isinstance(schema, D1Schema) or type(is_create) is not bool:
            raise ValueError('typed D1 schema and explicit creation mode required')
        self.schema = schema
        self.directory = (create_mock_d1_workspace(directory, schema.schema_id) if is_create
                          else require_mock_d1_workspace(directory, schema.schema_id))
        self._invoke([], schema_statements=schema.bootstrap)

    def execute(self, statements):
        frame = self._frame(statements)
        result = self._invoke([frame])
        return self._verify(result['operations'][0], len(statements))

    def execute_frames(self, frames):
        if type(frames) not in (tuple, list) or not 1 <= len(frames) <= 2048:
            raise ValueError('bounded D1 frames required')
        results = []
        for offset in range(0, len(frames), 32):
            group = frames[offset:offset + 32]
            if len(group) == 1:
                results.append(self.execute(group[0]))
                continue
            payload = {'schema_statements': [], 'operations': [self._frame(frame) for frame in group]}
            receipt = run_d1_object_session(payload, mock_directory=self.directory, schema_id=self.schema.schema_id)
            result = self._restore_runtime(receipt, is_object_session=True)
            if len(result['operations']) != len(group):
                raise ValueError('shared D1 frame count differs')
            results.extend(self._verify(operation, len(frame)) for operation, frame in zip(result['operations'], group))
        return results

    def _frame(self, statements):
        if type(statements) not in (tuple, list) or not 1 <= len(statements) <= 49:
            raise ValueError('D1 frame allows at most 49 statements plus schema guard')
        converted = []
        for statement in statements:
            if not hasattr(statement, 'sql') or not hasattr(statement, 'parameters'):
                raise ValueError('typed prepared D1 statement required')
            converted.append(D1Statement(statement.sql, statement.parameters).wire)
        frame = {'kind': 'batch', 'statements': [self.schema.guard().wire, *converted]}
        if len(canonical_json(frame).encode()) > 65536:
            raise ValueError('shared D1 control frame exceeds 64 KiB')
        return frame

    def _invoke(self, operations, *, schema_statements=()):
        payload = {'schema_statements': list(schema_statements), 'operations': operations}
        if len(canonical_json(payload).encode()) > 65536:
            raise ValueError('shared D1 invocation exceeds 64 KiB')
        receipt = run_d1_emulator(payload, mock_directory=self.directory, schema_id=self.schema.schema_id)
        result = self._restore_runtime(receipt)
        if len(result['operations']) != len(operations):
            raise ValueError('shared D1 operation count differs')
        return result

    @staticmethod
    def _restore_runtime(receipt, *, is_object_session=False):
        if receipt.get('outcome') != 'completed' or receipt.get('is_child_stopped') is not True:
            raise ValueError('shared D1 native outcome unproven')
        result = receipt['object_reply'] if is_object_session else json.loads(receipt['stdout'])
        if result.get('outcome') == 'emulator_failed':
            raise ValueError('shared D1 emulator failed: ' + str(result.get('error_message', ''))[:1000])
        if (result.get('is_mock') is not True or result.get('network_providers') != []
                or result.get('are_sdk_children_closed') is not True or result.get('is_environment_sanitized') is not True):
            raise ValueError('shared D1 runtime envelope differs')
        return result

    @staticmethod
    def _verify(operation, count):
        if (operation.get('outcome') != 'completed' or len(operation.get('results', [])) != count + 1
                or any(result.get('success') is not True for result in operation['results'])
                or operation['results'][0].get('results') != [{'schema_ok': 1}]):
            raise ValueError('shared D1 batch outcome unproven')
        return operation['results'][1:]
