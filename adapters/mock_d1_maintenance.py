"""Lock-scoped, thread-bound grants for fixed local mock migration frames."""

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading

from adapters.mock_d1_lock import _workspace_os_lock
from adapters.mock_d1_workspace import _inspect_mock_d1_workspace, _marker
from coordination.d1_migration_policy import migration_quiescence
from coordination.d1_migrations import (AdditiveMigrationPlan, LEDGER, apply_revision, begin_migration,
    finish_migration, history_guard, ready_guard)
from coordination.d1_schema import D1Schema
from coordination.d1_statement import D1Statement
from domain.snapshots import canonical_json

_ACTIVE = threading.local()


@dataclass(frozen=True)
class MaintenanceIntent:
    plan: AdditiveMigrationPlan
    base_revision: int
    base_epoch: int

    def __post_init__(self):
        begin_migration(self.plan, self.base_revision, self.base_epoch)
        for revision in range(self.base_revision, len(self.plan.schemas)):
            self.combined_schema(revision)
            migration_quiescence(self.plan, revision)
        for statements in (*self.read_frames, *self.write_frames):
            maintenance_payload(statements)

    @property
    def source_schema_id(self):
        return self.combined_schema(self.base_revision).schema_id

    @property
    def target_schema_id(self):
        return self.combined_schema(len(self.plan.schemas) - 1).schema_id

    @property
    def journal_bytes(self):
        return canonical_json({'schema_version': 1, 'is_mock': True, 'plan_id': self.plan.plan_id,
            'base_revision': self.base_revision, 'base_epoch': self.base_epoch,
            'source_schema_id': self.source_schema_id, 'target_schema_id': self.target_schema_id}).encode('utf-8')

    @property
    def metadata_frame(self):
        return (LEDGER.guard(), D1Statement('SELECT * FROM coordinator_schema_authority', ()),
            D1Statement('SELECT revision,checksum,before_id,after_id,applied_epoch FROM coordinator_schema_history ORDER BY revision LIMIT 18', ()))

    @property
    def read_frames(self):
        frames = [self.metadata_frame]
        for revision in range(self.base_revision, len(self.plan.schemas)):
            frames.append(self.validation_frame(revision))
            frames.append(self.inspection_frame(revision))
        target = len(self.plan.schemas) - 1
        frames.append((self.combined_schema(target).guard(), ready_guard(self.plan, target, self.base_epoch + 1)))
        return tuple(frames)

    @property
    def write_frames(self):
        return (begin_migration(self.plan, self.base_revision, self.base_epoch),
            *(apply_revision(self.plan, revision, self.base_epoch + 1)
              for revision in range(self.base_revision + 1, len(self.plan.schemas))),
            finish_migration(self.plan, self.base_epoch + 1))

    def combined_schema(self, revision):
        if type(revision) is not int or not 0 <= revision < len(self.plan.schemas):
            raise ValueError('schema revision is outside the maintenance plan')
        return D1Schema((*self.plan.schemas[revision].statements, *LEDGER.statements))

    def inspection_frame(self, revision):
        return (*self.validation_frame(revision), migration_quiescence(self.plan, revision))

    def validation_frame(self, revision):
        return (self.combined_schema(revision).guard(), history_guard(self.plan, revision))


def maintenance_payload(statements):
    if type(statements) not in (tuple, list) or not 1 <= len(statements) <= 50 or any(not isinstance(item, D1Statement) for item in statements):
        raise ValueError('bounded typed maintenance statements required')
    payload = {'schema_statements': [], 'operations': [{'kind': 'batch', 'statements': [item.wire for item in statements]}]}
    if len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')) > 65536:
        raise ValueError('maintenance control frame exceeds 64 KiB')
    return payload


@contextmanager
def mock_d1_maintenance_access(directory, intent, *, timeout=60):
    if not isinstance(intent, MaintenanceIntent):
        raise ValueError('typed fixed migration intent required')
    if getattr(_ACTIVE, 'capability', None) is not None:
        raise ValueError('nested mock D1 maintenance is unsupported')
    markers = (_marker(intent.source_schema_id), _marker(intent.target_schema_id))
    directory = _inspect_mock_d1_workspace(directory, markers, intent.journal_bytes)
    with _workspace_os_lock(directory, is_exclusive=True, timeout=timeout):
        directory = _inspect_mock_d1_workspace(directory, markers, intent.journal_bytes)
        capability = _MaintenanceCapability(intent, directory, threading.get_ident(),
            frozenset(_digest(maintenance_payload(frame)) for frame in intent.read_frames),
            frozenset(_digest(maintenance_payload(frame)) for frame in intent.write_frames))
        _ACTIVE.capability = capability
        try:
            yield capability
        finally:
            _ACTIVE.capability = None


def maintenance_storage(capability, directory, schema_id, payload):
    _require_active(capability)
    intent = capability.intent
    if Path(directory).absolute() != capability.directory or schema_id != intent.source_schema_id:
        raise ValueError('maintenance capability workspace differs')
    fingerprint = _digest(payload)
    if fingerprint not in capability.read_hashes | capability.write_hashes:
        raise ValueError('maintenance frame is outside the fixed plan')
    _inspect_mock_d1_workspace(capability.directory,
        (_marker(intent.source_schema_id), _marker(intent.target_schema_id)), intent.journal_bytes)
    if fingerprint in capability.write_hashes and not (capability.directory / 'maintenance-lock.json').is_file():
        raise ValueError('mutating migration requires the matching durable journal')
    return capability.directory / 'storage'


@dataclass(frozen=True)
class _MaintenanceCapability:
    intent: MaintenanceIntent
    directory: Path
    thread_id: int
    read_hashes: frozenset[str]
    write_hashes: frozenset[str]

    def fence(self):
        maintenance_storage(self, self.directory, self.intent.source_schema_id, maintenance_payload(self.intent.metadata_frame))
        path = self.directory / 'maintenance-lock.json'
        if not path.exists():
            _atomic_owned_write(path, self.intent.journal_bytes)


def _require_active(capability):
    if (type(capability) is not _MaintenanceCapability or getattr(_ACTIVE, 'capability', None) is not capability
            or capability.thread_id != threading.get_ident()):
        raise ValueError('active lock-scoped maintenance capability required')


def _atomic_owned_write(path, content):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='wb', dir=path.parent, prefix='.owned-d1-marker-', delete=False) as output:
            temporary = Path(output.name)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _digest(payload):
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()
