"""Readiness-pinned local D1 frames; upgrade orchestration remains separate."""

from adapters.local_d1_session import LocalD1Session
from adapters.mock_d1_workspace import create_mock_d1_workspace, require_mock_d1_workspace
from coordination.d1_migrations import AdditiveMigrationPlan, LEDGER, bootstrap, ready_guard
from coordination.d1_schema import D1Schema
from coordination.d1_statement import D1Statement
from domain.snapshots import canonical_json


class LocalD1VersionedSession(LocalD1Session):
    def __init__(self, directory, plan, revision, epoch, *, is_create=False):
        if not isinstance(plan, AdditiveMigrationPlan) or type(is_create) is not bool:
            raise ValueError('typed local migration plan and creation mode required')
        self._readiness = ready_guard(plan, revision, epoch)
        if is_create and (revision != 0 or epoch != 1):
            raise ValueError('new versioned workspace starts at revision zero and epoch one')
        self.schema = D1Schema((*plan.schemas[revision].statements, *LEDGER.statements))
        self.directory = (create_mock_d1_workspace(directory, self.schema.schema_id) if is_create
                          else require_mock_d1_workspace(directory, self.schema.schema_id))
        if is_create:
            self._invoke([], schema_statements=[item.wire for item in bootstrap(plan)])
        else:
            # Reopen is read-only; it must not repair missing DDL or reset history.
            self.execute((D1Statement('SELECT 1 AS reopened', ()),))

    def _frame(self, statements):
        if type(statements) not in (tuple, list) or not 1 <= len(statements) <= 48:
            raise ValueError('versioned D1 allows 48 statements plus two guards')
        frame = super()._frame(statements)
        frame['statements'].insert(1, self._readiness.wire)
        if len(canonical_json(frame).encode()) > 65536:
            raise ValueError('versioned D1 control frame exceeds 64 KiB')
        return frame

    @staticmethod
    def _verify(operation, count):
        results = operation.get('results') if type(operation) is dict else None
        if (type(results) is not list or operation.get('outcome') != 'completed' or len(results) != count + 2
                or any(type(result) is not dict or result.get('success') is not True for result in results)
                or not _is_guard_row(results[0].get('results'), 'schema_ok')
                or not _is_guard_row(results[1].get('results'), 'schema_ready')):
            raise ValueError('versioned D1 batch outcome unproven')
        return results[2:]


def _is_guard_row(rows, key):
    return (type(rows) is list and len(rows) == 1 and type(rows[0]) is dict and set(rows[0]) == {key}
            and type(rows[0][key]) is int and rows[0][key] == 1)
