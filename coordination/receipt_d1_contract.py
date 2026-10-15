"""Receipt-driven standalone CAS prototype with schema-pinned SQLite triggers."""

import hashlib

from application.tasks import StateConflict
from coordination.d1_cas_contract import BoundStatement,HeadCasIntent
from domain.snapshots import canonical_json

BEFORE_TRIGGER="""CREATE TRIGGER contract_receipt_before BEFORE INSERT ON contract_receipts
BEGIN
    SELECT CASE WHEN EXISTS(SELECT 1 FROM contract_receipts WHERE request_id=NEW.request_id) THEN
        CASE WHEN EXISTS(SELECT 1 FROM contract_receipts WHERE request_id=NEW.request_id
            AND signature=NEW.signature AND scope_id=NEW.scope_id AND family=NEW.family
            AND base_revision=NEW.base_revision AND version_id=NEW.version_id AND code_sha=NEW.code_sha
            AND actor_id=NEW.actor_id AND revision=NEW.revision)
        THEN RAISE(IGNORE) ELSE RAISE(ABORT,'request_intent_conflict') END
    WHEN EXISTS(SELECT 1 FROM contract_barriers WHERE scope_id IN (NEW.scope_id,'global') AND is_blocked=1)
        THEN RAISE(ABORT,'scope_barrier')
    WHEN COALESCE((SELECT revision FROM contract_heads WHERE scope_id=NEW.scope_id AND family=NEW.family),0)!=NEW.base_revision
        THEN RAISE(ABORT,'head_revision_conflict') END;
END"""

AFTER_TRIGGER="""CREATE TRIGGER contract_receipt_after AFTER INSERT ON contract_receipts
BEGIN
    INSERT INTO contract_heads(scope_id,family,version_id,code_sha,revision)
        VALUES(NEW.scope_id,NEW.family,NEW.version_id,NEW.code_sha,NEW.revision)
        ON CONFLICT(scope_id,family) DO UPDATE SET version_id=excluded.version_id,code_sha=excluded.code_sha,revision=excluded.revision
        WHERE contract_heads.revision=NEW.base_revision;
    SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM contract_heads WHERE scope_id=NEW.scope_id AND family=NEW.family
        AND version_id=NEW.version_id AND code_sha=NEW.code_sha AND revision=NEW.revision)
        THEN RAISE(ABORT,'head_write_unproven') END;
END"""

SCHEMA_STATEMENTS=(
    """CREATE TABLE contract_heads (
    scope_id TEXT NOT NULL, family TEXT NOT NULL, version_id TEXT NOT NULL, code_sha TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(typeof(revision)='integer' AND revision>0),
    PRIMARY KEY(scope_id,family)
) WITHOUT ROWID""",
    """CREATE TABLE contract_receipts (
    request_id TEXT PRIMARY KEY, signature TEXT NOT NULL, scope_id TEXT NOT NULL, family TEXT NOT NULL,
    base_revision INTEGER NOT NULL CHECK(typeof(base_revision)='integer' AND base_revision>=0),
    version_id TEXT NOT NULL, code_sha TEXT NOT NULL, actor_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(typeof(revision)='integer' AND revision=base_revision+1)
) WITHOUT ROWID""",
    """CREATE TABLE contract_barriers (
    scope_id TEXT PRIMARY KEY, is_blocked INTEGER NOT NULL CHECK(is_blocked IN (0,1))
) WITHOUT ROWID""",
    BEFORE_TRIGGER,AFTER_TRIGGER)

SCHEMA_ID=hashlib.sha256(canonical_json(SCHEMA_STATEMENTS).encode()).hexdigest()
SCHEMA_OBJECTS=tuple(zip(('table','table','table','trigger','trigger'),
    ('contract_heads','contract_receipts','contract_barriers','contract_receipt_before','contract_receipt_after'),SCHEMA_STATEMENTS))


def build_receipt_cas_batch(intent):
    if not isinstance(intent,HeadCasIntent):raise ValueError('typed immutable CAS intent required')
    read=BoundStatement('SELECT request_id,signature,scope_id,family,base_revision,version_id,code_sha,actor_id,revision '
        'FROM contract_receipts WHERE request_id=? AND signature=?',(intent.request_id,intent.signature))
    return (build_receipt_cas_mutation(intent),read)


def build_receipt_cas_mutation(intent):
    if not isinstance(intent,HeadCasIntent):raise ValueError('typed immutable CAS intent required')
    matches=' OR '.join('(type=? AND name=? AND sql=?)' for _ in SCHEMA_OBJECTS)
    expected=tuple(value for item in SCHEMA_OBJECTS for value in item)
    insert=BoundStatement('INSERT INTO contract_receipts(request_id,signature,scope_id,family,base_revision,version_id,code_sha,actor_id,revision) '
        'VALUES(?,CASE WHEN (SELECT COUNT(*) FROM sqlite_master WHERE '+matches+')=5 AND '
        "(SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND tbl_name IN ('contract_heads','contract_receipts','contract_barriers'))=2 "
        "THEN ? ELSE json('schema_mismatch') END,?,?,?,?,?,?,?)",
        (intent.request_id,*expected,intent.signature,intent.scope_id,intent.family,intent.expected_revision,
            intent.version_id,intent.code_sha,intent.actor_id,intent.expected_revision+1))
    return insert


def verify_receipt_cas_result(intent,results):
    if not isinstance(intent,HeadCasIntent):raise ValueError('typed immutable CAS intent required')
    if (not isinstance(results,list) or len(results)!=2 or any(not isinstance(row,dict) or row.get('success') is not True for row in results)
            or canonical_json(results[1].get('results'))!=canonical_json([intent.receipt])):
        raise StateConflict('receipt-driven CAS outcome unproven')
    return intent.receipt
