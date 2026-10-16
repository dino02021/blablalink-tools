"""Bounded local-compute recovery; never releases publication or hosted run holds."""

from dataclasses import asdict, dataclass
import hashlib

from coordination.d1_compute_queue import bounded_identifier, digest, timestamp
from coordination.d1_lease_actions import PINS_CURRENT
from coordination.d1_statement import D1Statement
from domain.snapshots import canonical_json


SCHEMA_STATEMENTS = (
    'CREATE TABLE compute_recovery_requests(request_id TEXT PRIMARY KEY,signature TEXT NOT NULL,scope_id TEXT,limit_rows INTEGER NOT NULL,created_at_ms INTEGER NOT NULL,item_count INTEGER NOT NULL) WITHOUT ROWID',
    'CREATE TABLE compute_recovery_items(request_id TEXT NOT NULL,job_id TEXT NOT NULL,generation INTEGER NOT NULL,target_state TEXT NOT NULL,reason TEXT NOT NULL,created_at_ms INTEGER NOT NULL,PRIMARY KEY(request_id,job_id)) WITHOUT ROWID',
    'CREATE TABLE compute_outcomes(job_id TEXT PRIMARY KEY,generation INTEGER NOT NULL,error_code TEXT NOT NULL,request_id TEXT NOT NULL,updated_at_ms INTEGER NOT NULL) WITHOUT ROWID',
    'CREATE INDEX compute_job_recovery ON compute_jobs(state,updated_at_ms,job_id)',
)

DEPENDENCY_FAILED = "EXISTS(SELECT 1 FROM compute_dependencies d JOIN compute_jobs p ON p.job_id=d.parent_id WHERE d.job_id=j.job_id AND p.state IN('failed','superseded'))"
ELIGIBLE = "((j.state='running' AND j.executor_type='local' AND j.expires_at_ms<=?) OR (j.state IN('queued','retry_wait') AND (" + DEPENDENCY_FAILED + ' OR NOT (' + PINS_CURRENT + '))))'


@dataclass(frozen=True)
class RecoveryIntent:
    request_id: str
    limit: int = 100
    scope_id: str | None = None

    def __post_init__(self):
        bounded_identifier(self.request_id)
        if self.scope_id is not None:
            bounded_identifier(self.scope_id)
        if type(self.limit) is not int or not 1 <= self.limit <= 100:
            raise ValueError('bounded recovery limit required')

    @property
    def signature(self):
        return hashlib.sha256(canonical_json(asdict(self)).encode()).hexdigest()


def build_recovery(intent, now_ms):
    if not isinstance(intent, RecoveryIntent):
        raise ValueError('typed recovery intent required')
    timestamp(now_ms)
    guard = D1Statement("SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM compute_recovery_requests WHERE request_id=?) OR EXISTS(SELECT 1 FROM compute_recovery_requests WHERE request_id=? AND signature=?) THEN 1 ELSE json('recovery_request_conflict') END AS recovery_ok", (intent.request_id, intent.request_id, intent.signature))
    exhausted = "j.state='running' AND j.attempt>=j.max_attempts"
    state = "CASE WHEN " + exhausted + " THEN 'failed' WHEN " + DEPENDENCY_FAILED + " THEN 'failed' WHEN NOT (" + PINS_CURRENT + ") THEN 'superseded' ELSE 'retry_wait' END"
    reason = "CASE WHEN " + exhausted + " THEN 'lease_expired' WHEN " + DEPENDENCY_FAILED + " THEN 'dependency_failed' WHEN NOT (" + PINS_CURRENT + ") THEN 'inputs_superseded' ELSE 'lease_expired' END"
    select = D1Statement('INSERT INTO compute_recovery_items SELECT ?,j.job_id,j.generation,' + state + ',' + reason + ',? FROM compute_jobs j WHERE (? IS NULL OR j.scope_id=?) AND NOT EXISTS(SELECT 1 FROM compute_recovery_requests WHERE request_id=?) AND ' + ELIGIBLE + ' ORDER BY j.updated_at_ms,j.job_id LIMIT ?',
        (intent.request_id, now_ms, intent.scope_id, intent.scope_id, intent.request_id, now_ms, intent.limit))
    update = D1Statement('UPDATE compute_jobs AS j SET state=(SELECT target_state FROM compute_recovery_items i WHERE i.request_id=? AND i.job_id=j.job_id),owner=NULL,expires_at_ms=NULL,updated_at_ms=? WHERE NOT EXISTS(SELECT 1 FROM compute_recovery_requests WHERE request_id=?) AND EXISTS(SELECT 1 FROM compute_recovery_items i WHERE i.request_id=? AND i.job_id=j.job_id AND i.generation=j.generation)',
        (intent.request_id, now_ms, intent.request_id, intent.request_id))
    outcomes = D1Statement('INSERT INTO compute_outcomes SELECT job_id,generation,reason,request_id,created_at_ms FROM compute_recovery_items WHERE request_id=? AND NOT EXISTS(SELECT 1 FROM compute_recovery_requests WHERE request_id=?) ON CONFLICT(job_id) DO UPDATE SET generation=excluded.generation,error_code=excluded.error_code,request_id=excluded.request_id,updated_at_ms=excluded.updated_at_ms WHERE compute_outcomes.generation<=excluded.generation', (intent.request_id, intent.request_id))
    proof = D1Statement("SELECT CASE WHEN EXISTS(SELECT 1 FROM compute_recovery_requests WHERE request_id=? AND signature=?) OR NOT EXISTS(SELECT 1 FROM compute_recovery_items i LEFT JOIN compute_jobs j ON j.job_id=i.job_id LEFT JOIN compute_outcomes o ON o.job_id=i.job_id WHERE i.request_id=? AND (j.job_id IS NULL OR j.state!=i.target_state OR j.generation!=i.generation OR j.owner IS NOT NULL OR j.expires_at_ms IS NOT NULL OR j.updated_at_ms!=i.created_at_ms OR o.job_id IS NULL OR o.generation!=i.generation OR o.error_code!=i.reason OR o.request_id!=i.request_id OR o.updated_at_ms!=i.created_at_ms)) THEN 1 ELSE json('recovery_write_unproven') END AS recovery_ok",
        (intent.request_id, intent.signature, intent.request_id))
    receipt = D1Statement('INSERT INTO compute_recovery_requests SELECT ?,?,?,?,?,COUNT(*) FROM compute_recovery_items WHERE request_id=? AND NOT EXISTS(SELECT 1 FROM compute_recovery_requests WHERE request_id=?) HAVING COUNT(*)>0',
        (intent.request_id, intent.signature, intent.scope_id, intent.limit, now_ms, intent.request_id, intent.request_id))
    return (guard, select, update, outcomes, proof, receipt, *read_recovery(intent.request_id))


def read_recovery(request_id):
    bounded_identifier(request_id)
    return (D1Statement('SELECT * FROM compute_recovery_requests WHERE request_id=?', (request_id,)),
            D1Statement('SELECT * FROM compute_recovery_items WHERE request_id=? ORDER BY job_id LIMIT 100', (request_id,)))


def verify_recovery(intent, header_rows, items):
    if not isinstance(intent, RecoveryIntent) or type(header_rows) is not list or type(items) is not list:
        raise ValueError('typed recovery result required')
    if not header_rows:
        if items:
            raise ValueError('unreceipted recovery items')
        return {'receipt': None, 'items': []}
    if len(header_rows) != 1:
        raise ValueError('recovery receipt cardinality differs')
    header = header_rows[0]
    expected = {'request_id': intent.request_id, 'signature': intent.signature, 'scope_id': intent.scope_id, 'limit_rows': intent.limit}
    if (type(header) is not dict or set(header) != {*expected, 'created_at_ms', 'item_count'}
            or any(header[key] != value for key, value in expected.items()) or type(header['limit_rows']) is not int
            or type(header['item_count']) is not int or header['item_count'] != len(items) or not 1 <= len(items) <= intent.limit):
        raise ValueError('recovery receipt differs from intent')
    timestamp(header['created_at_ms'])
    seen = set()
    for item in items:
        if type(item) is not dict or set(item) != {'request_id','job_id','generation','target_state','reason','created_at_ms'}:
            raise ValueError('invalid recovery item')
        digest(item['job_id'])
        if (item['request_id'] != intent.request_id or item['job_id'] in seen or type(item['generation']) is not int
                or not 0 <= item['generation'] < 2 ** 31 or type(item['created_at_ms']) is not int
                or item['created_at_ms'] != header['created_at_ms'] or item['target_state'] not in {'retry_wait','failed','superseded'}
                or item['reason'] not in {'lease_expired','dependency_failed','inputs_superseded'}):
            raise ValueError('recovery item differs')
        seen.add(item['job_id'])
    return {'receipt': dict(header), 'items': [dict(item) for item in items]}
