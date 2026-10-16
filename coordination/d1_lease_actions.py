"""Receipt-preserving heartbeat and sealed-result completion batches."""

from dataclasses import asdict, dataclass
import hashlib

from application.tasks import ComputeLease
from coordination.d1_compute_queue import bounded_identifier, digest, timestamp
from coordination.d1_statement import D1Statement
from domain.snapshots import canonical_json


PINS_CURRENT = "NOT EXISTS(SELECT 1 FROM compute_source_pins p WHERE p.job_id=j.job_id AND NOT EXISTS(SELECT 1 FROM contract_heads h WHERE h.scope_id=p.scope_id AND h.family=p.family AND h.version_id=p.version_id AND h.revision=p.revision AND h.code_sha=p.code_sha))"


@dataclass(frozen=True)
class ComputeActionIntent:
    request_id: str
    lease: ComputeLease
    action: str
    lease_ms: int | None = None
    result_id: str | None = None
    is_publication_required: bool = False

    def __post_init__(self):
        bounded_identifier(self.request_id)
        validate_lease(self.lease)
        if type(self.is_publication_required) is not bool:
            raise ValueError('explicit publication completion mode required')
        if self.action == 'heartbeat':
            if type(self.lease_ms) is not int or not 1000 <= self.lease_ms <= 300000 or self.result_id is not None or self.is_publication_required:
                raise ValueError('invalid heartbeat intent')
        elif self.action == 'complete':
            digest(self.result_id)
            if self.lease_ms is not None:
                raise ValueError('completion must not extend a compute lease')
        else:
            raise ValueError('unsupported compute lease action')

    @property
    def signature(self):
        return hashlib.sha256(canonical_json(asdict(self)).encode()).hexdigest()


def build_action(intent, now_ms):
    if not isinstance(intent, ComputeActionIntent):
        raise ValueError('typed compute action intent required')
    timestamp(now_ms)
    lease = intent.lease
    identity = (lease.job_id, lease.worker_id, lease.code_sha, lease.generation)
    fresh = "EXISTS(SELECT 1 FROM compute_jobs WHERE job_id=? AND state='running' AND owner=? AND code_sha=? AND generation=? AND expires_at_ms>?)"
    bindings = (*identity, now_ms)
    if intent.action == 'complete':
        fresh += ' AND EXISTS(SELECT 1 FROM object_heads h JOIN object_transports t ON t.transport_id=h.transport_id WHERE h.object_id=? AND t.object_id=h.object_id AND t.is_sealed=1)'
        fresh += ' AND EXISTS(SELECT 1 FROM compute_jobs WHERE job_id=? AND (result_id IS NULL OR result_id=?))'
        bindings += (intent.result_id, lease.job_id, intent.result_id)
    receipt_match = 'request_id=? AND signature=? AND action=? AND job_id=? AND worker_id=? AND code_sha=? AND generation=?'
    receipt_bindings = (intent.request_id, intent.signature, intent.action, *identity)
    guard = D1Statement("SELECT CASE WHEN EXISTS(SELECT 1 FROM compute_actions WHERE request_id=?) THEN CASE WHEN EXISTS(SELECT 1 FROM compute_actions WHERE " + receipt_match + ") THEN 1 ELSE json('compute_action_request_conflict') END WHEN " + fresh + " THEN 1 ELSE json('compute_lease_action_rejected') END AS action_ok",
                        (intent.request_id, *receipt_bindings, *bindings))
    if intent.action == 'heartbeat':
        expiry = now_ms + intent.lease_ms
        timestamp(expiry)
        update = D1Statement('UPDATE compute_jobs SET expires_at_ms=MAX(expires_at_ms,?),updated_at_ms=? WHERE job_id=? AND NOT EXISTS(SELECT 1 FROM compute_actions WHERE request_id=?)', (expiry, now_ms, lease.job_id, intent.request_id))
        post = "j.state='running' AND j.owner=? AND j.code_sha=? AND j.generation=? AND j.expires_at_ms>=?"
        post_bindings = (lease.worker_id, lease.code_sha, lease.generation, expiry)
    else:
        target = 'computed' if intent.is_publication_required else 'succeeded'
        state_expression = "CASE WHEN " + PINS_CURRENT + " THEN ? ELSE 'superseded' END"
        update = D1Statement('UPDATE compute_jobs AS j SET result_id=?,state=' + state_expression + ',owner=NULL,expires_at_ms=NULL,updated_at_ms=? WHERE job_id=? AND NOT EXISTS(SELECT 1 FROM compute_actions WHERE request_id=?)',
                             (intent.result_id, target, now_ms, lease.job_id, intent.request_id))
        post = 'j.state=' + state_expression + ' AND j.result_id=? AND j.owner IS NULL AND j.expires_at_ms IS NULL AND j.code_sha=? AND j.generation=?'
        post_bindings = (target, intent.result_id, lease.code_sha, lease.generation)
    insert = D1Statement('INSERT INTO compute_actions SELECT ?,?,?,?,?,?,?,j.state,j.expires_at_ms,j.result_id,? FROM compute_jobs j WHERE j.job_id=? AND NOT EXISTS(SELECT 1 FROM compute_actions WHERE request_id=?) AND ' + post,
                         (*receipt_bindings, now_ms, lease.job_id, intent.request_id, *post_bindings))
    proof = D1Statement("SELECT CASE WHEN EXISTS(SELECT 1 FROM compute_actions WHERE " + receipt_match + ") THEN 1 ELSE json('compute_action_write_unproven') END AS action_ok", receipt_bindings)
    return (guard, update, insert, proof, read_action(intent.request_id))


def read_action(request_id):
    bounded_identifier(request_id)
    return D1Statement('SELECT * FROM compute_actions WHERE request_id=?', (request_id,))


def active_lease(lease, now_ms):
    validate_lease(lease)
    timestamp(now_ms)
    return D1Statement("SELECT job_id FROM compute_jobs WHERE job_id=? AND state='running' AND owner=? AND code_sha=? AND generation=? AND expires_at_ms>?",
        (lease.job_id, lease.worker_id, lease.code_sha, lease.generation, now_ms))


def verify_action(intent, rows):
    if not isinstance(intent, ComputeActionIntent):
        raise ValueError('typed compute action intent required')
    if type(rows) is not list or len(rows) != 1 or type(rows[0]) is not dict:
        raise ValueError('exact compute action receipt required')
    row = rows[0]
    expected = {'request_id': intent.request_id, 'signature': intent.signature, 'action': intent.action,
                'job_id': intent.lease.job_id, 'worker_id': intent.lease.worker_id,
                'code_sha': intent.lease.code_sha, 'generation': intent.lease.generation}
    if (set(row) != {*expected, 'outcome_state', 'expires_at_ms', 'result_id', 'created_at_ms'}
            or any(row[key] != value for key, value in expected.items()) or type(row['generation']) is not int):
        raise ValueError('compute action receipt differs from intent')
    timestamp(row['created_at_ms'])
    if intent.action == 'heartbeat':
        timestamp(row['expires_at_ms'])
        if row['outcome_state'] != 'running' or row['expires_at_ms'] < row['created_at_ms'] + intent.lease_ms:
            raise ValueError('heartbeat receipt did not prove renewal')
        if row['result_id'] is not None:
            digest(row['result_id'])
    elif (row['outcome_state'] not in ({'computed', 'superseded'} if intent.is_publication_required else {'succeeded', 'superseded'})
          or row['result_id'] != intent.result_id or row['expires_at_ms'] is not None):
        raise ValueError('completion receipt did not prove sealed result transition')
    return dict(row)


def validate_lease(lease):
    if not isinstance(lease, ComputeLease):
        raise ValueError('typed compute lease required')
    digest(lease.job_id)
    bounded_identifier(lease.worker_id)
    bounded_identifier(lease.code_sha)
    if not 1 <= lease.generation < 2 ** 31:
        raise ValueError('bounded compute generation required')
