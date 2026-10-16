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

    def get_job(self, job_id):
        digest(job_id)
        rows = self._run((D1Statement('SELECT * FROM compute_jobs WHERE job_id=?', (job_id,)),))[0]['results']
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
                   row['result_id'], None, row['next_attempt_at_ms'] / 1000)

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
