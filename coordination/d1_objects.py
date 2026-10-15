"""Immutable D1 transport statements; Python performs all content verification."""

from dataclasses import dataclass
import hashlib
import json

from coordination.d1_binary import D1Binary
from domain.chunks import ChunkManifest, verify_chunk
from domain.snapshots import canonical_json


SCHEMA_STATEMENTS = (
    'CREATE TABLE IF NOT EXISTS object_transports(transport_id TEXT PRIMARY KEY,object_id TEXT NOT NULL,manifest TEXT NOT NULL,is_sealed INTEGER NOT NULL DEFAULT 0 CHECK(is_sealed IN(0,1))) WITHOUT ROWID',
    'CREATE INDEX IF NOT EXISTS object_transport_identity ON object_transports(object_id)',
    'CREATE TABLE IF NOT EXISTS object_chunks(transport_id TEXT NOT NULL,ordinal INTEGER NOT NULL CHECK(ordinal>=0 AND ordinal<512),chunk_hash TEXT NOT NULL,chunk_data BLOB NOT NULL CHECK(length(chunk_data)>0 AND length(chunk_data)<=32768),PRIMARY KEY(transport_id,ordinal)) WITHOUT ROWID',
    'CREATE TABLE IF NOT EXISTS object_heads(object_id TEXT PRIMARY KEY,transport_id TEXT NOT NULL) WITHOUT ROWID',
)
SCHEMA_ID = hashlib.sha256(canonical_json(SCHEMA_STATEMENTS).encode()).hexdigest()


@dataclass(frozen=True)
class ObjectStatement:
    sql: str
    parameters: tuple

    def __post_init__(self):
        if type(self.sql) is not str or not 0 < len(self.sql.encode()) <= 4096 or len(self.parameters) > 100:
            raise ValueError('bounded D1 object statement required')
        for value in self.parameters:
            if value is not None and type(value) not in (str, int) and not isinstance(value, D1Binary):
                raise ValueError('invalid D1 object binding')
        if len(canonical_json(self.wire).encode()) > 65536:
            raise ValueError('D1 object control frame exceeds 64 KiB')

    @property
    def wire(self):
        return {'sql': self.sql, 'parameters': [value.wire if isinstance(value, D1Binary) else value for value in self.parameters]}


def transport_id(manifest):
    if not isinstance(manifest, ChunkManifest):
        raise ValueError('validated manifest required')
    return hashlib.sha256(manifest.serialized.encode()).hexdigest()


def validate_schema():
    names = ('object_transports', 'object_transport_identity', 'object_chunks', 'object_heads')
    predicates = ' AND '.join('EXISTS(SELECT 1 FROM sqlite_master WHERE name=? AND sql=?)' for _ in names)
    parameters = tuple(value for name, sql in zip(names, SCHEMA_STATEMENTS)
                       for value in (name, sql.replace(' IF NOT EXISTS', '')))
    sql = "SELECT CASE WHEN (SELECT COUNT(*) FROM sqlite_master WHERE tbl_name IN('object_transports','object_chunks','object_heads'))=4 AND " + predicates + " THEN 1 ELSE json('object_schema_mismatch') END AS schema_ok"
    return ObjectStatement(sql, parameters)


def begin_transport(manifest):
    identity = transport_id(manifest)
    return ObjectStatement('INSERT INTO object_transports(transport_id,object_id,manifest) '
        'SELECT ?,?,? WHERE (SELECT COUNT(*) FROM object_transports WHERE object_id=?)<4 '
        'ON CONFLICT(transport_id) DO NOTHING', (identity, manifest.object_id, manifest.serialized, manifest.object_id))


def read_transport(identity):
    return ObjectStatement('SELECT * FROM object_transports WHERE transport_id=?', (_digest(identity),))


def put_chunk(manifest, ordinal, data):
    verify_chunk(manifest, ordinal, data)
    return ObjectStatement('INSERT INTO object_chunks(transport_id,ordinal,chunk_hash,chunk_data) '
        'SELECT ?,?,?,? WHERE EXISTS(SELECT 1 FROM object_transports WHERE transport_id=? AND is_sealed=0) '
        'ON CONFLICT(transport_id,ordinal) DO NOTHING',
        (transport_id(manifest), ordinal, manifest.chunk_hashes[ordinal], D1Binary(data), transport_id(manifest)))


def read_chunk(identity, ordinal):
    if type(ordinal) is not int or not 0 <= ordinal < 512:
        raise ValueError('invalid chunk ordinal')
    return ObjectStatement('SELECT ordinal,chunk_hash,chunk_data FROM object_chunks WHERE transport_id=? AND ordinal=?', (_digest(identity), ordinal))


def list_chunks(identity):
    return ObjectStatement('SELECT ordinal,chunk_hash,length(chunk_data) AS size FROM object_chunks WHERE transport_id=? ORDER BY ordinal', (_digest(identity),))


def seal_verified_transport(manifest):
    """Only the trusted Python port may call this after reading and decoding all chunks.

    SQL checks manifest/count metadata; this is not authentication or a hash proof.
    No public Worker endpoint is provided by this module.
    """
    identity = transport_id(manifest)
    return (
        ObjectStatement('UPDATE object_transports SET is_sealed=1 WHERE transport_id=? AND manifest=? '
            'AND (SELECT COUNT(*) FROM object_chunks WHERE transport_id=?)=?',
            (identity, manifest.serialized, identity, len(manifest.chunk_hashes))),
        ObjectStatement('INSERT INTO object_heads(object_id,transport_id) SELECT object_id,transport_id '
            'FROM object_transports WHERE transport_id=? AND is_sealed=1 ON CONFLICT(object_id) DO NOTHING', (identity,)),
    )


def read_object_head(object_id):
    return ObjectStatement('SELECT t.* FROM object_heads h JOIN object_transports t ON t.transport_id=h.transport_id '
        'WHERE h.object_id=? AND t.object_id=h.object_id AND t.is_sealed=1', (_digest(object_id),))


def restore_manifest(row):
    if type(row) is not dict or set(row) != {'transport_id', 'object_id', 'manifest', 'is_sealed'} or type(row['is_sealed']) is not int or row['is_sealed'] not in (0, 1):
        raise ValueError('invalid D1 transport row')
    try:
        manifest = ChunkManifest(**json.loads(row['manifest']))
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError('invalid D1 transport manifest') from error
    if manifest.serialized != row['manifest'] or manifest.object_id != row['object_id'] or transport_id(manifest) != row['transport_id']:
        raise ValueError('D1 transport identity differs')
    return manifest


def _digest(value):
    if type(value) is not str or len(value) != 64 or any(character not in '0123456789abcdef' for character in value):
        raise ValueError('SHA-256 identity required')
    return value
