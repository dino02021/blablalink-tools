"""Frozen compute queue core on one local D1; not a LocalStateStore replacement."""

from dataclasses import asdict
import time

from adapters.local_d1_objects import LocalD1Objects
from adapters.local_d1_session import LocalD1Session
from application.tasks import ComputeLease, Job, LeaseLost, StateConflict, TaskSpec
from coordination.d1_compute_queue import (SCHEMA_STATEMENTS, active_claim, bounded_identifier,
    claim, digest, enqueue_frozen, timestamp)
from coordination.d1_schema import D1Schema
from coordination.d1_statement import D1Statement
from coordination.receipt_d1_contract import SCHEMA_STATEMENTS as HEAD_SCHEMA
from coordination.d1_lease_actions import ComputeActionIntent, active_lease, build_action, read_action, verify_action
from coordination.d1_checkpoints import (SCHEMA_STATEMENTS as CHECKPOINT_SCHEMA, CheckpointIntent, CheckpointVersion,
    build_checkpoint, checkpoint_object_size, read_checkpoint, read_receipt, verify_checkpoint_receipt)
from coordination.d1_recovery import SCHEMA_STATEMENTS as RECOVERY_SCHEMA, RecoveryIntent, build_recovery, read_recovery, verify_recovery
from domain.snapshots import canonical_json


class LocalD1ComputeQueue:
    def __init__(self, session, *, clock_ms=None):
        if not isinstance(session, LocalD1Session) or not set(D1Schema((*HEAD_SCHEMA, *SCHEMA_STATEMENTS)).objects).issubset(set(session.schema.objects)):
            raise ValueError('shared D1 session must own the complete compute queue schema')
        if clock_ms is not None and not callable(clock_ms):
            raise ValueError('trusted local test clock must be callable')
        self.session = session
        self.objects = LocalD1Objects.with_session(session)
        self._clock = clock_ms if clock_ms is not None else lambda: time.time_ns() // 1000000

    def enqueue_frozen(self, request_id, spec, pins=()):
        statements, signature = enqueue_frozen(request_id, spec, pins, self._now())
        existing = self._run((D1Statement('SELECT * FROM compute_requests WHERE request_id=?', (request_id,)),))[0]['results']
        if existing:
            self._verify_request(existing, request_id, spec.fingerprint, signature)
            return self.get_job(spec.fingerprint)
        value = self.objects.read(spec.request_signature)
        if canonical_json(value) != canonical_json(asdict(spec)):
            raise StateConflict('frozen task object differs from requested spec')
        result = self._run(statements)
        self._verify_request(result[-1]['results'], request_id, spec.fingerprint, signature)
        return self.get_job(spec.fingerprint)

    def claim(self, intent):
        results = self._run(claim(intent, self._now()))
        rows = results[-1]['results']
        if not rows:
            return None
        if len(rows) != 1:
            raise StateConflict('compute claim receipt cardinality differs')
        try:
            proof = active_claim(intent, rows[0], self._now())
        except ValueError as error:
            raise StateConflict('compute claim receipt integrity differs') from error
        active = self._run((proof,))[0]['results']
        if active != [{'job_id': rows[0]['job_id']}]:
            raise LeaseLost('recorded compute claim is no longer active; use a fresh request for new work')
        return ComputeLease(rows[0]['job_id'], intent.worker_id, intent.code_sha, rows[0]['generation'])

    def claim_receipt(self, request_id):
        bounded_identifier(request_id)
        rows = self._run((D1Statement('SELECT * FROM compute_claims WHERE request_id=?', (request_id,)),))[0]['results']
        if len(rows) > 1:
            raise StateConflict('compute claim receipt cardinality differs')
        return rows[0] if rows else None

    def heartbeat(self, request_id, lease, *, lease_ms=60000):
        intent = ComputeActionIntent(request_id, lease, 'heartbeat', lease_ms=lease_ms)
        receipt = self._apply_action(intent)
        self._require_active(lease)
        return receipt

    def complete(self, request_id, lease, result_id, *, is_publication_required=True):
        intent = ComputeActionIntent(request_id, lease, 'complete', result_id=result_id,
                                     is_publication_required=is_publication_required)
        self._apply_action(intent)
        return self.get_job(lease.job_id)

    def save_checkpoint(self, request_id, lease, object_id, *, base_revision):
        self._require_checkpoint_schema()
        intent = CheckpointIntent(request_id, lease, object_id, base_revision)
        existing = self._run((read_receipt(request_id),))[0]['results']
        if existing:
            try:
                return verify_checkpoint_receipt(intent, existing)
            except ValueError as error:
                raise StateConflict('checkpoint request replay differs') from error
        self._require_active(lease)
        self._read_checkpoint_object(object_id)
        results = self._run(build_checkpoint(intent, self._now()))
        try:
            return verify_checkpoint_receipt(intent, results[-1]['results'])
        except ValueError as error:
            raise StateConflict('checkpoint receipt unproven') from error

    def load_checkpoint_version(self, job_id):
        self._require_checkpoint_schema()
        rows = self._run((read_checkpoint(job_id),))[0]['results']
        if not rows:
            raise KeyError('unknown D1 compute job')
        if len(rows) != 1:
            raise StateConflict('checkpoint metadata cardinality differs')
        row = rows[0]
        if row['object_id'] is None:
            if any(row[key] is not None for key in ('code_sha','generation','revision','updated_at_ms')):
                raise StateConflict('partial checkpoint metadata')
            return None
        if (row['code_sha'] != row['job_code_sha'] or type(row['generation']) is not int
                or type(row['job_generation']) is not int or row['generation'] > row['job_generation']):
            raise StateConflict('checkpoint code or generation differs from immutable job')
        timestamp(row['updated_at_ms'])
        value = self._read_checkpoint_object(row['object_id'])
        try:
            return CheckpointVersion(job_id, row['object_id'], row['code_sha'], row['generation'], row['revision'], canonical_json(value))
        except ValueError as error:
            raise StateConflict('checkpoint content metadata differs') from error

    def recover(self, request_id, *, limit=100, scope_id=None):
        if not self._owns_schema(RECOVERY_SCHEMA):
            raise ValueError('recovery schema capability is not installed')
        intent = RecoveryIntent(request_id, limit, scope_id)
        prior = self._run(read_recovery(request_id))
        if prior[0]['results'] or prior[1]['results']:
            try:
                return verify_recovery(intent, prior[0]['results'], prior[1]['results'])
            except ValueError as error:
                raise StateConflict('recovery request replay differs') from error
        result = self._run(build_recovery(intent, self._now()))
        try:
            return verify_recovery(intent, result[-2]['results'], result[-1]['results'])
        except ValueError as error:
            raise StateConflict('recovery receipt unproven') from error

    def get_job(self, job_id):
        digest(job_id)
        sql = ('SELECT j.*,o.error_code AS queue_error_code FROM compute_jobs j LEFT JOIN compute_outcomes o ON o.job_id=j.job_id AND o.generation=j.generation WHERE j.job_id=?'
               if self._owns_schema(RECOVERY_SCHEMA) else 'SELECT j.*,NULL AS queue_error_code FROM compute_jobs j WHERE j.job_id=?')
        rows = self._run((D1Statement(sql, (job_id,)),))[0]['results']
        if len(rows) != 1:
            raise KeyError('unknown D1 compute job')
        row = rows[0]
        try:
            spec = TaskSpec(**self.objects.read(row['spec_id']))
        except (ValueError, TypeError) as error:
            raise StateConflict('stored frozen TaskSpec differs') from error
        fields = ('command', 'scope_id', 'family', 'code_sha', 'executor_type', 'priority', 'max_attempts')
        if spec.fingerprint != job_id or spec.request_signature != row['spec_id'] or any(getattr(spec, field) != row[field] for field in fields):
            raise StateConflict('D1 job metadata differs from frozen TaskSpec')
        return Job(job_id, spec, row['state'], row['attempt'], row['generation'], row['owner'],
                   row['expires_at_ms'] / 1000 if row['expires_at_ms'] is not None else None,
                   row['result_id'], row['queue_error_code'], row['next_attempt_at_ms'] / 1000)

    def _now(self):
        value = self._clock()
        timestamp(value)
        return value

    def _apply_action(self, intent):
        existing = self._run((read_action(intent.request_id),))[0]['results']
        if existing:
            try:
                return verify_action(intent, existing)
            except ValueError as error:
                raise StateConflict('compute action request replay differs') from error
        self._require_active(intent.lease)
        results = self._run(build_action(intent, self._now()))
        try:
            return verify_action(intent, results[-1]['results'])
        except ValueError as error:
            raise StateConflict('compute action receipt unproven') from error

    def _require_active(self, lease):
        rows = self._run((active_lease(lease, self._now()),))[0]['results']
        if rows != [{'job_id': lease.job_id}]:
            raise LeaseLost('compute lease is no longer active')

    def _require_checkpoint_schema(self):
        if not self._owns_schema(CHECKPOINT_SCHEMA):
            raise ValueError('checkpoint schema capability is not installed')

    def _owns_schema(self, statements):
        return set(D1Schema(statements).objects).issubset(set(self.session.schema.objects))

    def _read_checkpoint_object(self, object_id):
        rows = self._run((checkpoint_object_size(object_id),))[0]['results']
        if len(rows) != 1 or type(rows[0].get('size')) is not int or not 1 <= rows[0]['size'] <= 32768:
            raise StateConflict('bounded sealed checkpoint object required')
        value = self.objects.read(object_id)
        if type(value) is not dict or value.get('checkpoint_kind') in ('training_collection','training_collection_pointer'):
            raise StateConflict('generic checkpoint cannot replace specialized collection progress validation')
        return value

    def _run(self, statements):
        try:
            return self.session.execute(statements)
        except ValueError as error:
            raise StateConflict('D1 queue transaction rejected or outcome unproven') from error

    @staticmethod
    def _verify_request(rows, request_id, job_id, signature):
        if (len(rows) != 1 or set(rows[0]) != {'request_id', 'signature', 'job_id', 'created_at_ms'}
                or rows[0]['request_id'] != request_id or rows[0]['job_id'] != job_id or rows[0]['signature'] != signature):
            raise StateConflict('D1 enqueue receipt differs')
        timestamp(rows[0]['created_at_ms'])
