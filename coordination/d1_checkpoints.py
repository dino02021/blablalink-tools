"""Generation-fenced checkpoint heads and immutable request receipts."""

from dataclasses import asdict, dataclass
import hashlib
import json

from application.tasks import ComputeLease
from coordination.d1_compute_queue import bounded_identifier, digest, timestamp
from coordination.d1_lease_actions import validate_lease
from coordination.d1_statement import D1Statement
from domain.snapshots import canonical_json


SCHEMA_STATEMENTS = (
    'CREATE TABLE compute_checkpoints(job_id TEXT PRIMARY KEY,object_id TEXT NOT NULL,code_sha TEXT NOT NULL,generation INTEGER NOT NULL,revision INTEGER NOT NULL CHECK(revision>0),updated_at_ms INTEGER NOT NULL) WITHOUT ROWID',
    'CREATE TABLE compute_checkpoint_receipts(request_id TEXT PRIMARY KEY,signature TEXT NOT NULL,job_id TEXT NOT NULL,object_id TEXT NOT NULL,worker_id TEXT NOT NULL,code_sha TEXT NOT NULL,generation INTEGER NOT NULL,revision INTEGER NOT NULL,created_at_ms INTEGER NOT NULL) WITHOUT ROWID',
)


@dataclass(frozen=True)
class CheckpointIntent:
    request_id: str
    lease: ComputeLease
    object_id: str
    base_revision: int

    def __post_init__(self):
        bounded_identifier(self.request_id)
        validate_lease(self.lease)
        digest(self.object_id)
        if type(self.base_revision) is not int or not 0 <= self.base_revision < 2 ** 31 - 1:
            raise ValueError('bounded checkpoint base revision required')

    @property
    def signature(self):
        return hashlib.sha256(canonical_json(asdict(self)).encode()).hexdigest()


@dataclass(frozen=True)
class CheckpointVersion:
    job_id: str
    object_id: str
    code_sha: str
    generation: int
    revision: int
    serialized: str

    def __post_init__(self):
        digest(self.job_id)
        digest(self.object_id)
        bounded_identifier(self.code_sha)
        for value in (self.generation, self.revision):
            if type(value) is not int or not 1 <= value < 2 ** 31:
                raise ValueError('bounded checkpoint generation/revision required')
        if type(self.serialized) is not str or len(self.serialized.encode()) > 32768:
            raise ValueError('bounded checkpoint JSON required')
        value = json.loads(self.serialized)
        if (type(value) is not dict or canonical_json(value) != self.serialized
                or hashlib.sha256(self.serialized.encode()).hexdigest() != self.object_id):
            raise ValueError('checkpoint content identity differs')

    @property
    def payload(self):
        return json.loads(self.serialized)


def build_checkpoint(intent, now_ms):
    if not isinstance(intent, CheckpointIntent):
        raise ValueError('typed checkpoint intent required')
    timestamp(now_ms)
    lease = intent.lease
    receipt_predicate = 'request_id=? AND signature=? AND job_id=? AND object_id=? AND worker_id=? AND code_sha=? AND generation=? AND revision=?'
    receipt_args = (intent.request_id, intent.signature, lease.job_id, intent.object_id, lease.worker_id,
                    lease.code_sha, lease.generation, intent.base_revision + 1)
    guard = D1Statement("SELECT CASE WHEN EXISTS(SELECT 1 FROM compute_checkpoint_receipts WHERE request_id=?) THEN CASE WHEN EXISTS(SELECT 1 FROM compute_checkpoint_receipts WHERE " + receipt_predicate + ") THEN 1 ELSE json('checkpoint_request_conflict') END WHEN EXISTS(SELECT 1 FROM compute_jobs WHERE job_id=? AND state='running' AND owner=? AND code_sha=? AND generation=? AND expires_at_ms>?) AND EXISTS(SELECT 1 FROM object_heads h JOIN object_transports t ON t.transport_id=h.transport_id WHERE h.object_id=? AND t.object_id=h.object_id AND t.is_sealed=1) AND COALESCE((SELECT revision FROM compute_checkpoints WHERE job_id=?),0)=? THEN 1 ELSE json('checkpoint_lease_or_revision_conflict') END AS checkpoint_ok",
        (intent.request_id, *receipt_args, lease.job_id, lease.worker_id, lease.code_sha, lease.generation, now_ms, intent.object_id, lease.job_id, intent.base_revision))
    write = D1Statement('INSERT INTO compute_checkpoints SELECT ?,?,?,?,?,? WHERE NOT EXISTS(SELECT 1 FROM compute_checkpoint_receipts WHERE request_id=?) ON CONFLICT(job_id) DO UPDATE SET object_id=excluded.object_id,code_sha=excluded.code_sha,generation=excluded.generation,revision=excluded.revision,updated_at_ms=excluded.updated_at_ms WHERE compute_checkpoints.revision=?',
        (lease.job_id, intent.object_id, lease.code_sha, lease.generation, intent.base_revision + 1, now_ms, intent.request_id, intent.base_revision))
    receipt = D1Statement('INSERT INTO compute_checkpoint_receipts SELECT ?,?,?,?,?,?,?,?,? WHERE NOT EXISTS(SELECT 1 FROM compute_checkpoint_receipts WHERE request_id=?) AND EXISTS(SELECT 1 FROM compute_checkpoints WHERE job_id=? AND object_id=? AND code_sha=? AND generation=? AND revision=?)',
        (*receipt_args, now_ms, intent.request_id, lease.job_id, intent.object_id, lease.code_sha, lease.generation, intent.base_revision + 1))
    proof = D1Statement("SELECT CASE WHEN EXISTS(SELECT 1 FROM compute_checkpoint_receipts WHERE " + receipt_predicate + ") THEN 1 ELSE json('checkpoint_write_unproven') END AS checkpoint_ok", receipt_args)
    return (guard, write, receipt, proof, read_receipt(intent.request_id))


def read_receipt(request_id):
    bounded_identifier(request_id)
    return D1Statement('SELECT * FROM compute_checkpoint_receipts WHERE request_id=?', (request_id,))


def read_checkpoint(job_id):
    digest(job_id)
    return D1Statement('SELECT j.job_id,j.code_sha AS job_code_sha,j.generation AS job_generation,c.object_id,c.code_sha,c.generation,c.revision,c.updated_at_ms FROM compute_jobs j LEFT JOIN compute_checkpoints c ON c.job_id=j.job_id WHERE j.job_id=?', (job_id,))


def checkpoint_object_size(object_id):
    digest(object_id)
    return D1Statement("SELECT json_extract(t.manifest,'$.raw_size') AS size FROM object_heads h JOIN object_transports t ON t.transport_id=h.transport_id WHERE h.object_id=? AND t.object_id=h.object_id AND t.is_sealed=1", (object_id,))


def verify_checkpoint_receipt(intent, rows):
    if not isinstance(intent, CheckpointIntent) or type(rows) is not list or len(rows) != 1:
        raise ValueError('exact checkpoint receipt required')
    expected = {'request_id': intent.request_id, 'signature': intent.signature, 'job_id': intent.lease.job_id,
                'object_id': intent.object_id, 'worker_id': intent.lease.worker_id, 'code_sha': intent.lease.code_sha,
                'generation': intent.lease.generation, 'revision': intent.base_revision + 1}
    row = rows[0]
    if (type(row) is not dict or set(row) != {*expected, 'created_at_ms'} or any(row[key] != value for key, value in expected.items())
            or type(row['generation']) is not int or type(row['revision']) is not int):
        raise ValueError('checkpoint receipt differs from intent')
    timestamp(row['created_at_ms'])
    return dict(row)
