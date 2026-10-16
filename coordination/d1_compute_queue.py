"""Atomic frozen-task queue primitives; no dispatch, publisher or provider API."""

from dataclasses import asdict, dataclass
import hashlib

from application.tasks import TaskSpec, validate_identifier
from coordination.d1_statement import D1Statement
from domain.snapshots import canonical_json

FROZEN_COMMANDS = frozenset({'prepare_config', 'refresh_roster_and_training', 'refresh_raid_history'})

CLAIM_BEFORE = """CREATE TRIGGER compute_claim_before BEFORE INSERT ON compute_claims
BEGIN
 SELECT CASE WHEN EXISTS(SELECT 1 FROM compute_claims WHERE request_id=NEW.request_id) THEN
  CASE WHEN EXISTS(SELECT 1 FROM compute_claims WHERE request_id=NEW.request_id AND signature=NEW.signature
   AND job_id=NEW.job_id AND worker_id=NEW.worker_id AND code_sha=NEW.code_sha AND executor_type=NEW.executor_type
   AND lease_ms=NEW.lease_ms AND generation=NEW.generation AND expires_at_ms=NEW.expires_at_ms)
   THEN RAISE(IGNORE) ELSE RAISE(ABORT,'claim_request_conflict') END END;
END"""
CLAIM_AFTER = """CREATE TRIGGER compute_claim_after AFTER INSERT ON compute_claims
BEGIN
 UPDATE compute_jobs SET state='running',owner=NEW.worker_id,generation=NEW.generation,
  expires_at_ms=NEW.expires_at_ms,attempt=attempt+1,updated_at_ms=NEW.expires_at_ms-NEW.lease_ms
 WHERE job_id=NEW.job_id AND generation=NEW.generation-1 AND state IN('queued','retry_wait')
  AND attempt<max_attempts AND code_sha=NEW.code_sha AND executor_type=NEW.executor_type;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM compute_jobs WHERE job_id=NEW.job_id AND state='running'
  AND owner=NEW.worker_id AND generation=NEW.generation AND expires_at_ms=NEW.expires_at_ms
  AND code_sha=NEW.code_sha AND executor_type=NEW.executor_type) THEN RAISE(ABORT,'claim_write_unproven') END;
END"""

SCHEMA_STATEMENTS = (
    "CREATE TABLE compute_jobs(job_id TEXT PRIMARY KEY,spec_id TEXT NOT NULL,input_signature TEXT NOT NULL,command TEXT NOT NULL,scope_id TEXT NOT NULL,family TEXT NOT NULL,code_sha TEXT NOT NULL,executor_type TEXT NOT NULL CHECK(executor_type IN('local','actions','vercel')),priority INTEGER NOT NULL CHECK(priority BETWEEN -1000 AND 1000),max_attempts INTEGER NOT NULL CHECK(max_attempts BETWEEN 1 AND 10),state TEXT NOT NULL CHECK(state IN('queued','running','retry_wait','computed','succeeded','superseded','failed')),attempt INTEGER NOT NULL DEFAULT 0,generation INTEGER NOT NULL DEFAULT 0,owner TEXT,expires_at_ms INTEGER,result_id TEXT,next_attempt_at_ms INTEGER NOT NULL,created_at_ms INTEGER NOT NULL,updated_at_ms INTEGER NOT NULL) WITHOUT ROWID",
    'CREATE INDEX compute_job_ready ON compute_jobs(state,code_sha,executor_type,next_attempt_at_ms,priority)',
    'CREATE TABLE compute_dependencies(job_id TEXT NOT NULL,parent_id TEXT NOT NULL,PRIMARY KEY(job_id,parent_id),CHECK(job_id!=parent_id)) WITHOUT ROWID',
    'CREATE TABLE compute_source_pins(job_id TEXT NOT NULL,input_name TEXT NOT NULL,scope_id TEXT NOT NULL,family TEXT NOT NULL,version_id TEXT NOT NULL,revision INTEGER NOT NULL,code_sha TEXT NOT NULL,PRIMARY KEY(job_id,input_name)) WITHOUT ROWID',
    'CREATE TABLE compute_requests(request_id TEXT PRIMARY KEY,signature TEXT NOT NULL,job_id TEXT NOT NULL,created_at_ms INTEGER NOT NULL) WITHOUT ROWID',
    'CREATE TABLE compute_claims(request_id TEXT PRIMARY KEY,signature TEXT NOT NULL,job_id TEXT NOT NULL,worker_id TEXT NOT NULL,code_sha TEXT NOT NULL,executor_type TEXT NOT NULL,lease_ms INTEGER NOT NULL,generation INTEGER NOT NULL,expires_at_ms INTEGER NOT NULL) WITHOUT ROWID',
    "CREATE TABLE compute_actions(request_id TEXT PRIMARY KEY,signature TEXT NOT NULL,action TEXT NOT NULL CHECK(action IN('heartbeat','complete')),job_id TEXT NOT NULL,worker_id TEXT NOT NULL,code_sha TEXT NOT NULL,generation INTEGER NOT NULL,outcome_state TEXT NOT NULL,expires_at_ms INTEGER,result_id TEXT,created_at_ms INTEGER NOT NULL) WITHOUT ROWID",
    CLAIM_BEFORE, CLAIM_AFTER,
)


@dataclass(frozen=True)
class SourcePin:
    input_name: str
    scope_id: str
    family: str
    version_id: str
    revision: int
    code_sha: str

    def __post_init__(self):
        for value in (self.input_name, self.scope_id, self.family, self.code_sha):
            bounded_identifier(value)
        digest(self.version_id)
        if type(self.revision) is not int or not 1 <= self.revision < 2 ** 31:
            raise ValueError('bounded source head revision required')


@dataclass(frozen=True)
class ClaimIntent:
    request_id: str
    worker_id: str
    code_sha: str
    lease_ms: int = 60000
    executor_type: str = 'local'

    def __post_init__(self):
        for value in (self.request_id, self.worker_id, self.code_sha):
            bounded_identifier(value)
        if self.executor_type != 'local':
            raise ValueError('hosted claims require the future dispatch admission contract')
        if type(self.lease_ms) is not int or not 1000 <= self.lease_ms <= 300000:
            raise ValueError('bounded compute lease duration required')

    @property
    def signature(self):
        return hashlib.sha256(canonical_json(asdict(self)).encode()).hexdigest()


def enqueue_frozen(request_id, spec, pins, now_ms):
    bounded_identifier(request_id)
    timestamp(now_ms)
    if not isinstance(spec, TaskSpec) or len(spec.dependencies) > 300 or len(canonical_json(asdict(spec)).encode()) > 32768:
        raise ValueError('bounded immutable TaskSpec required')
    if spec.command not in FROZEN_COMMANDS:
        raise ValueError('command requires the future domain freshness or publication contract')
    for value in (spec.scope_id, spec.family, spec.code_sha):
        bounded_identifier(value)
    if type(pins) not in (tuple, list) or len(pins) > 16 or any(not isinstance(pin, SourcePin) for pin in pins):
        raise ValueError('bounded typed source pins required')
    pins = tuple(sorted(pins, key=lambda pin: pin.input_name))
    if len({pin.input_name for pin in pins}) != len(pins) or any(spec.version_map.get(pin.input_name) != pin.version_id for pin in pins):
        raise ValueError('source pins must match unique task inputs')
    pin_json = canonical_json([asdict(pin) for pin in pins])
    input_signature = hashlib.sha256(pin_json.encode()).hexdigest()
    signature = hashlib.sha256(canonical_json({'spec_id': spec.request_signature, 'input_signature': input_signature}).encode()).hexdigest()
    deps = canonical_json(spec.dependencies)
    fresh = "EXISTS(SELECT 1 FROM object_heads h JOIN object_transports t ON t.transport_id=h.transport_id WHERE h.object_id=? AND t.object_id=h.object_id AND t.is_sealed=1) "
    fresh += "AND NOT EXISTS(SELECT 1 FROM json_each(?) p WHERE NOT EXISTS(SELECT 1 FROM contract_heads h WHERE h.scope_id=json_extract(p.value,'$.scope_id') AND h.family=json_extract(p.value,'$.family') AND h.version_id=json_extract(p.value,'$.version_id') AND h.revision=json_extract(p.value,'$.revision') AND h.code_sha=json_extract(p.value,'$.code_sha'))) "
    fresh += "AND NOT EXISTS(SELECT 1 FROM json_each(?) d WHERE NOT EXISTS(SELECT 1 FROM compute_jobs j WHERE j.job_id=d.value)) "
    fresh += "AND NOT EXISTS(SELECT 1 FROM compute_jobs WHERE job_id=? AND (spec_id!=? OR input_signature!=?))"
    guard = D1Statement("SELECT CASE WHEN EXISTS(SELECT 1 FROM compute_requests WHERE request_id=?) THEN CASE WHEN EXISTS(SELECT 1 FROM compute_requests WHERE request_id=? AND signature=? AND job_id=?) THEN 1 ELSE json('queue_request_conflict') END WHEN " + fresh + " THEN 1 ELSE json('frozen_enqueue_rejected') END AS enqueue_ok",
        (request_id, request_id, signature, spec.fingerprint, spec.request_signature, pin_json, deps, spec.fingerprint, spec.request_signature, input_signature))
    insert = D1Statement("INSERT INTO compute_jobs(job_id,spec_id,input_signature,command,scope_id,family,code_sha,executor_type,priority,max_attempts,state,next_attempt_at_ms,created_at_ms,updated_at_ms) SELECT ?,?,?,?,?,?,?,?,?,?,'queued',?,?,? WHERE NOT EXISTS(SELECT 1 FROM compute_requests WHERE request_id=?) ON CONFLICT(job_id) DO NOTHING",
        (spec.fingerprint, spec.request_signature, input_signature, spec.command, spec.scope_id, spec.family, spec.code_sha, spec.executor_type, spec.priority, spec.max_attempts, now_ms, now_ms, now_ms, request_id))
    dependencies = D1Statement('INSERT INTO compute_dependencies SELECT ?,value FROM json_each(?) WHERE NOT EXISTS(SELECT 1 FROM compute_requests WHERE request_id=?) ON CONFLICT(job_id,parent_id) DO NOTHING', (spec.fingerprint, deps, request_id))
    sources = D1Statement("INSERT INTO compute_source_pins SELECT ?,json_extract(value,'$.input_name'),json_extract(value,'$.scope_id'),json_extract(value,'$.family'),json_extract(value,'$.version_id'),json_extract(value,'$.revision'),json_extract(value,'$.code_sha') FROM json_each(?) WHERE NOT EXISTS(SELECT 1 FROM compute_requests WHERE request_id=?) ON CONFLICT(job_id,input_name) DO NOTHING", (spec.fingerprint, pin_json, request_id))
    receipt = D1Statement('INSERT INTO compute_requests SELECT ?,?,?,? WHERE NOT EXISTS(SELECT 1 FROM compute_requests WHERE request_id=?)', (request_id, signature, spec.fingerprint, now_ms, request_id))
    read = D1Statement('SELECT * FROM compute_requests WHERE request_id=?', (request_id,))
    return (guard, insert, dependencies, sources, receipt, read), signature


def claim(intent, now_ms):
    if not isinstance(intent, ClaimIntent):
        raise ValueError('typed compute claim intent required')
    timestamp(now_ms)
    timestamp(now_ms + intent.lease_ms)
    eligible = "j.state IN('queued','retry_wait') AND j.next_attempt_at_ms<=? AND j.attempt<j.max_attempts AND j.generation<2147483647 AND j.code_sha=? AND j.executor_type=? "
    eligible += "AND NOT EXISTS(SELECT 1 FROM compute_dependencies d LEFT JOIN compute_jobs p ON p.job_id=d.parent_id WHERE d.job_id=j.job_id AND (p.job_id IS NULL OR p.state!='succeeded')) "
    eligible += "AND NOT EXISTS(SELECT 1 FROM compute_source_pins p WHERE p.job_id=j.job_id AND NOT EXISTS(SELECT 1 FROM contract_heads h WHERE h.scope_id=p.scope_id AND h.family=p.family AND h.version_id=p.version_id AND h.revision=p.revision AND h.code_sha=p.code_sha))"
    sql = 'INSERT INTO compute_claims SELECT ?,?,j.job_id,?,?,?,?,j.generation+1,? FROM compute_jobs j WHERE j.job_id=(SELECT j.job_id FROM compute_jobs j WHERE ' + eligible
    sql += " ORDER BY j.priority DESC,j.created_at_ms,j.job_id LIMIT 1) AND NOT EXISTS(SELECT 1 FROM compute_claims WHERE request_id=?) UNION ALL SELECT ?,?,job_id,?,?,?,?,generation,expires_at_ms FROM compute_claims WHERE request_id=?"
    bindings = (intent.request_id, intent.signature, intent.worker_id, intent.code_sha, intent.executor_type, intent.lease_ms, now_ms + intent.lease_ms,
                now_ms, intent.code_sha, intent.executor_type, intent.request_id,
                intent.request_id, intent.signature, intent.worker_id, intent.code_sha, intent.executor_type, intent.lease_ms, intent.request_id)
    return (D1Statement(sql, bindings), D1Statement('SELECT * FROM compute_claims WHERE request_id=?', (intent.request_id,)))


def active_claim(intent, receipt, now_ms):
    timestamp(now_ms)
    if type(receipt) is not dict or set(receipt) != {'request_id','signature','job_id','worker_id','code_sha','executor_type','lease_ms','generation','expires_at_ms'}:
        raise ValueError('exact compute claim receipt required')
    expected = asdict(intent)
    if any(receipt[key] != value for key, value in expected.items()) or receipt['signature'] != intent.signature:
        raise ValueError('compute claim receipt differs from intent')
    digest(receipt['job_id'])
    if type(receipt['generation']) is not int or not 1 <= receipt['generation'] < 2 ** 31:
        raise ValueError('invalid compute claim generation')
    timestamp(receipt['expires_at_ms'])
    return D1Statement("SELECT job_id FROM compute_jobs WHERE job_id=? AND state='running' AND owner=? AND code_sha=? AND executor_type=? AND generation=? AND expires_at_ms>=? AND expires_at_ms>?",
        (receipt['job_id'], intent.worker_id, intent.code_sha, intent.executor_type, receipt['generation'], receipt['expires_at_ms'], now_ms))


def bounded_identifier(value):
    validate_identifier(value)
    if len(value.encode()) > 128:
        raise ValueError('bounded coordinator identifier required')


def digest(value):
    if type(value) is not str or len(value) != 64 or any(character not in '0123456789abcdef' for character in value):
        raise ValueError('SHA-256 identity required')


def timestamp(value):
    if type(value) is not int or not 0 <= value <= 2 ** 53 - 1:
        raise ValueError('exact nonnegative epoch milliseconds required')
