"""Resumable, exclusively owned mock D1 upgrade; no restore unfreezing."""

from dataclasses import dataclass
import json
import re

from adapters.local_d1_session import LocalD1Session
from adapters.mock_d1_maintenance import (MaintenanceIntent, _atomic_owned_write, maintenance_payload,
    maintenance_storage, mock_d1_maintenance_access)
from adapters.mock_d1_workspace import _marker
from application.tasks import StateConflict
from coordination.d1_migrations import apply_revision, begin_migration, finish_migration, verify_revision_receipt
from domain.snapshots import canonical_json
from executors.local.emulator_process import run_d1_maintenance_batch


def upgrade_mock_d1_workspace(directory, intent):
    if not isinstance(intent, MaintenanceIntent):
        raise ValueError('typed fixed mock migration intent required')
    with mock_d1_maintenance_access(directory, intent) as capability:
        state = _read_state(capability)
        _invoke(capability, intent.validation_frame(state.revision))
        marker_id = json.loads((capability.directory / 'mock-d1.json').read_text(encoding='utf-8'))['schema_id']
        if marker_id == intent.target_schema_id and state.phase != 'complete':
            raise StateConflict('target marker precedes authoritative D1 readiness')
        if state.phase == 'complete':
            _verify_ready(capability)
            if marker_id == intent.target_schema_id and not (capability.directory / 'maintenance-lock.json').exists():
                return _receipt(capability)
            _invoke(capability, intent.inspection_frame(state.revision))
            capability.fence()
        else:
            _invoke(capability, intent.inspection_frame(state.revision))
            # D1 is authoritative. A lost local journal can be recreated only
            # after the complete plan/history/schema/hold proofs above succeed.
            capability.fence()
            if state.phase == 'ready_before':
                _invoke(capability, begin_migration(intent.plan, intent.base_revision, intent.base_epoch))
            for revision in range(state.revision + 1, len(intent.plan.schemas)):
                _invoke(capability, apply_revision(intent.plan, revision, intent.base_epoch + 1))
            _invoke(capability, finish_migration(intent.plan, intent.base_epoch + 1))
            completed = _read_state(capability)
            if completed.phase != 'complete':
                raise StateConflict('migration final authority is not ready')
            _verify_ready(capability)
        _finish_files(capability)
        return _receipt(capability)


@dataclass(frozen=True)
class _MigrationState:
    revision: int
    phase: str


def _read_state(capability):
    results = _invoke(capability, capability.intent.metadata_frame)
    return _verify_state(capability.intent, results[1]['results'], results[2]['results'])


def _verify_state(intent, rows, history):
    if type(rows) is not list or len(rows) != 1 or type(rows[0]) is not dict:
        raise StateConflict('one migration authority row required')
    row = rows[0]
    fields = {'singleton', 'revision', 'schema_id', 'epoch', 'state', 'plan_id', 'target_revision', 'target_schema_id'}
    target = len(intent.plan.schemas) - 1
    if (set(row) != fields or type(row['singleton']) is not int or row['singleton'] != 1
            or type(row['revision']) is not int or not intent.base_revision <= row['revision'] <= target
            or type(row['epoch']) is not int
            or row['schema_id'] != intent.plan.schemas[row['revision']].schema_id):
        raise StateConflict('migration authority identity differs')
    revision = row['revision']
    if type(history) is not list or len(history) != revision + 1:
        raise StateConflict('migration history is incomplete or exceeds the plan')
    epochs = []
    try:
        for index, item in enumerate(history):
            epochs.append(verify_revision_receipt(intent.plan, index, [item])['applied_epoch'])
    except ValueError as error:
        raise StateConflict('migration checksum history differs') from error
    if (epochs[0] != 1 or epochs[intent.base_revision] != intent.base_epoch
            or any(right - left not in (0, 1) for left, right in zip(epochs, epochs[1:]))
            or any(epoch > intent.base_epoch for epoch in epochs[:intent.base_revision + 1])
            or any(epoch != intent.base_epoch + 1 for epoch in epochs[intent.base_revision + 1:])):
        raise StateConflict('migration history epoch differs')
    if row['state'] == 'ready' and all(row[key] is None for key in ('plan_id', 'target_revision', 'target_schema_id')):
        if revision == intent.base_revision and row['epoch'] == intent.base_epoch:
            return _MigrationState(revision, 'ready_before')
        if revision == target and row['epoch'] == intent.base_epoch + 1:
            return _MigrationState(revision, 'complete')
    if (row['state'] == 'migrating' and row['epoch'] == intent.base_epoch + 1
            and row['plan_id'] == intent.plan.plan_id and type(row['target_revision']) is int
            and row['target_revision'] == target and row['target_schema_id'] == intent.plan.schemas[-1].schema_id):
        return _MigrationState(revision, 'migrating')
    raise StateConflict('migration phase or epoch differs from the fixed intent')


def _invoke(capability, statements):
    receipt = run_d1_maintenance_batch(statements, capability)
    try:
        result = LocalD1Session._restore_runtime(receipt)
        operations = result['operations']
        if type(operations) is not list or len(operations) != 1 or type(operations[0]) is not dict:
            raise ValueError('one migration batch response required')
        operation = operations[0]
        results = operation.get('results')
        if (operation.get('outcome') != 'completed' or type(results) is not list or len(results) != len(statements)
                or any(type(item) is not dict or item.get('success') is not True for item in results)):
            raise ValueError('migration batch is rejected or unproven')
        for statement, item in zip(statements, results):
            alias = re.search(r'\bAS (schema_ok|schema_ready|history_ok|migration_ok|migration_quiescent)\s*$', statement.sql)
            if alias is not None:
                key, rows = alias.group(1), item.get('results')
                if (type(rows) is not list or len(rows) != 1 or type(rows[0]) is not dict or set(rows[0]) != {key}
                        or type(rows[0][key]) is not int or rows[0][key] != 1):
                    raise ValueError('migration guard proof differs')
        return results
    except (ValueError, KeyError, TypeError) as error:
        raise StateConflict('mock D1 migration outcome unproven; preserve the matching journal') from error


def _verify_ready(capability):
    _invoke(capability, capability.intent.read_frames[-1])


def _finish_files(capability):
    intent = capability.intent
    maintenance_storage(capability, capability.directory, intent.source_schema_id, maintenance_payload(intent.metadata_frame))
    path = capability.directory / 'mock-d1.json'
    if json.loads(path.read_text(encoding='utf-8')) != _marker(intent.target_schema_id):
        _atomic_owned_write(path, canonical_json(_marker(intent.target_schema_id)).encode('utf-8'))
    # Both marker and durable journal are rechecked before removing this fence.
    maintenance_storage(capability, capability.directory, intent.source_schema_id, maintenance_payload(intent.metadata_frame))
    journal = capability.directory / 'maintenance-lock.json'
    if not journal.is_file() or journal.read_bytes() != intent.journal_bytes:
        raise StateConflict('matching maintenance journal required for completion')
    journal.unlink()


def _receipt(capability):
    intent = capability.intent
    return {'is_mock': True, 'plan_id': intent.plan.plan_id, 'base_revision': intent.base_revision,
            'target_revision': len(intent.plan.schemas) - 1, 'epoch': intent.base_epoch + 1,
            'schema_id': intent.target_schema_id, 'execution_mode': 'ready', 'workspace': str(capability.directory)}
