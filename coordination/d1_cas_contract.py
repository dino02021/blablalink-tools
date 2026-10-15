"""Bounded prepared-statement batch contract; no Worker entry or remote D1 client."""

from dataclasses import asdict,dataclass
import hashlib
import re

from application.tasks import StateConflict,validate_identifier
from domain.snapshots import canonical_json


@dataclass(frozen=True)
class BoundStatement:
    sql:str
    parameters:tuple

    def __post_init__(self):
        if isinstance(self.parameters,(str,bytes)):raise ValueError('explicit positional bindings required')
        parameters=tuple(self.parameters)
        if not isinstance(self.sql,str) or not 1<=len(self.sql.encode())<=4096 or len(parameters)>100:
            raise ValueError('bounded prepared statement required')
        if any(type(value) not in (str,int) or isinstance(value,str) and len(value.encode())>4096 for value in parameters):
            raise ValueError('bounded text or exact integer bindings required')
        if any(type(value) is int and abs(value)>2**53-1 for value in parameters):
            raise ValueError('exact integer bindings required')
        object.__setattr__(self,'parameters',parameters)


@dataclass(frozen=True)
class HeadCasIntent:
    request_id:str
    scope_id:str
    family:str
    expected_revision:int
    version_id:str
    code_sha:str
    actor_id:str

    def __post_init__(self):
        for value in (self.request_id,self.scope_id,self.family,self.code_sha,self.actor_id):
            validate_identifier(value)
            if len(value.encode())>128:raise ValueError('bounded CAS identifiers required')
        if (type(self.expected_revision) is not int or not 0<=self.expected_revision<2**31-1
                or not isinstance(self.version_id,str) or re.fullmatch('[a-f0-9]{64}',self.version_id) is None):
            raise ValueError('fixed bounded revision and version hash required')

    @property
    def signature(self):
        return hashlib.sha256(canonical_json(asdict(self)).encode()).hexdigest()

    @property
    def receipt(self):
        return {'request_id':self.request_id,'signature':self.signature,'scope_id':self.scope_id,'family':self.family,
            'base_revision':self.expected_revision,'version_id':self.version_id,'code_sha':self.code_sha,
            'actor_id':self.actor_id,'revision':self.expected_revision+1}


def build_head_cas_batch(intent):
    if not isinstance(intent,HeadCasIntent):raise ValueError('typed immutable CAS intent required')
    request,signature,scope,family=intent.request_id,intent.signature,intent.scope_id,intent.family
    statements=(
        BoundStatement('INSERT INTO contract_cas_guards(request_id,signature,is_allowed) VALUES(?,?,CASE '
            'WHEN EXISTS(SELECT 1 FROM contract_receipts WHERE request_id=?) THEN '
            'CASE WHEN EXISTS(SELECT 1 FROM contract_receipts WHERE request_id=? AND signature=?) THEN 1 ELSE 0 END '
            "WHEN EXISTS(SELECT 1 FROM contract_barriers WHERE scope_id IN (?, 'global') AND is_blocked=1) THEN 0 "
            'WHEN COALESCE((SELECT revision FROM contract_heads WHERE scope_id=? AND family=?),0)=? THEN 1 ELSE 0 END)',
            (request,signature,request,request,signature,scope,scope,family,intent.expected_revision)),
        BoundStatement('INSERT INTO contract_heads(scope_id,family,version_id,code_sha,revision) '
            'SELECT ?,?,?,?,? WHERE EXISTS(SELECT 1 FROM contract_cas_guards WHERE request_id=? AND signature=?) '
            'AND NOT EXISTS(SELECT 1 FROM contract_receipts WHERE request_id=?) '
            'ON CONFLICT(scope_id,family) DO UPDATE SET version_id=excluded.version_id,code_sha=excluded.code_sha,revision=excluded.revision '
            'WHERE contract_heads.revision=excluded.revision-1',
            (scope,family,intent.version_id,intent.code_sha,intent.expected_revision+1,request,signature,request)),
        BoundStatement('UPDATE contract_cas_guards SET is_allowed=CASE '
            'WHEN EXISTS(SELECT 1 FROM contract_receipts WHERE request_id=? AND signature=?) THEN 1 '
            'WHEN EXISTS(SELECT 1 FROM contract_heads WHERE scope_id=? AND family=? AND revision=? AND version_id=? AND code_sha=?) '
            'THEN 1 ELSE 0 END WHERE request_id=? AND signature=?',
            (request,signature,scope,family,intent.expected_revision+1,intent.version_id,intent.code_sha,request,signature)),
        BoundStatement('INSERT OR IGNORE INTO contract_receipts(request_id,signature,scope_id,family,base_revision,version_id,code_sha,actor_id,revision) '
            'SELECT ?,?,?,?,?,?,?,?,? WHERE EXISTS(SELECT 1 FROM contract_cas_guards WHERE request_id=? AND signature=?)',
            (request,signature,scope,family,intent.expected_revision,intent.version_id,intent.code_sha,intent.actor_id,intent.expected_revision+1,request,signature)),
        BoundStatement('UPDATE contract_cas_guards SET is_allowed=CASE WHEN EXISTS('
            'SELECT 1 FROM contract_receipts WHERE request_id=? AND signature=? AND scope_id=? AND family=? '
            'AND base_revision=? AND version_id=? AND code_sha=? AND actor_id=? AND revision=?) THEN 1 ELSE 0 END '
            'WHERE request_id=? AND signature=?',
            (request,signature,scope,family,intent.expected_revision,intent.version_id,intent.code_sha,intent.actor_id,intent.expected_revision+1,request,signature)),
        BoundStatement('SELECT request_id,signature,scope_id,family,base_revision,version_id,code_sha,actor_id,revision '
            'FROM contract_receipts WHERE request_id=? AND signature=?',(request,signature)),
        BoundStatement('DELETE FROM contract_cas_guards WHERE request_id=? AND signature=?',(request,signature)))
    if len(canonical_json([asdict(statement) for statement in statements]).encode())>65536:
        raise ValueError('CAS batch exceeds byte bound')
    return statements


def build_head_cas_lookup(intent):
    if not isinstance(intent,HeadCasIntent):raise ValueError('typed immutable CAS intent required')
    return BoundStatement('SELECT request_id,signature,scope_id,family,base_revision,version_id,code_sha,actor_id,revision '
        'FROM contract_receipts WHERE request_id=?',(intent.request_id,))


def verify_head_cas_lookup_result(intent,result):
    if not isinstance(intent,HeadCasIntent):raise ValueError('typed immutable CAS intent required')
    if (not isinstance(result,dict) or result.get('success') is not True or not isinstance(result.get('results'),list)
            or len(result['results'])>1):
        raise StateConflict('CAS receipt lookup outcome unproven')
    if not result['results']:return None
    if canonical_json(result['results'][0])!=canonical_json(intent.receipt):
        raise StateConflict('request already binds another or corrupted immutable receipt')
    return intent.receipt


def verify_head_cas_batch_result(intent,results):
    # A transport timeout must not be interpreted as a rejected or completed CAS.
    # Only a complete batch response with the exact immutable receipt is accepted.
    if not isinstance(intent,HeadCasIntent):raise ValueError('typed immutable CAS intent required')
    if (not isinstance(results,list) or len(results)!=7 or any(not isinstance(row,dict) or row.get('success') is not True for row in results)
            or canonical_json(results[5].get('results'))!=canonical_json([intent.receipt])):
        raise StateConflict('CAS outcome unproven; reconcile the same request without changing intent')
    return intent.receipt


def sum_d1_query_usage(results):
    if not isinstance(results,(list,tuple)) or not 1<=len(results)<=50:
        raise StateConflict('bounded nonempty D1 usage evidence required')
    counts={'rows_read':0,'rows_written':0}
    for result in results:
        if not isinstance(result,dict) or result.get('success') is not True:
            raise StateConflict('D1 usage query outcome is incomplete')
        meta=result.get('meta')
        if not isinstance(meta,dict):raise StateConflict('D1 local usage metadata is incomplete')
        for field in counts:
            value=meta.get(field)
            if type(value) is not int or not 0<=value<=2**53-1:
                raise StateConflict('D1 local usage metadata is incomplete')
            counts[field]+=value
            if counts[field]>2**53-1:raise StateConflict('D1 usage total exceeds exact range')
    return counts
