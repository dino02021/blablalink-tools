"""Temporary/local SQLite transport staging, separate from production providers."""

import json
import sqlite3
from pathlib import Path

from application.tasks import StateConflict
from domain.chunks import ChunkManifest, decode_document, verify_chunk


class LocalChunkStore:
    def __init__(self, path: Path) -> None:
        self.connection = sqlite3.connect(str(path), timeout=5)
        self.connection.execute("PRAGMA foreign_keys=ON")
        with self.connection:
            self.connection.execute("CREATE TABLE IF NOT EXISTS chunk_manifests(object_id TEXT PRIMARY KEY, manifest TEXT NOT NULL, sealed INTEGER NOT NULL DEFAULT 0)")
            self.connection.execute("CREATE TABLE IF NOT EXISTS object_chunks(object_id TEXT NOT NULL REFERENCES chunk_manifests(object_id), ordinal INTEGER NOT NULL, content BLOB NOT NULL, PRIMARY KEY(object_id, ordinal))")

    def close(self) -> None:
        self.connection.close()

    def begin(self, manifest: ChunkManifest) -> None:
        with self.connection:
            self.connection.execute("INSERT OR IGNORE INTO chunk_manifests(object_id,manifest) VALUES(?,?)", (manifest.object_id, manifest.serialized))
            if self._manifest(manifest.object_id).serialized != manifest.serialized:
                raise StateConflict("object already has another manifest")

    def put(self, object_id: str, index: int, chunk: bytes) -> None:
        manifest = self._manifest(object_id)
        verify_chunk(manifest, index, chunk)
        with self.connection:
            self.connection.execute("INSERT OR IGNORE INTO object_chunks VALUES(?,?,?)", (object_id, index, chunk))
            actual = self.connection.execute("SELECT content FROM object_chunks WHERE object_id=? AND ordinal=?", (object_id, index)).fetchone()[0]
            if actual != chunk:
                raise StateConflict("chunk cannot be replaced")

    def missing(self, object_id: str) -> tuple[int, ...]:
        manifest = self._manifest(object_id)
        existing = {row[0] for row in self.connection.execute("SELECT ordinal FROM object_chunks WHERE object_id=?", (object_id,))}
        return tuple(i for i in range(len(manifest.chunk_hashes)) if i not in existing)

    def seal(self, object_id: str):
        with self.connection:
            document = self._decode(object_id)
            self.connection.execute("UPDATE chunk_manifests SET sealed=1 WHERE object_id=?", (object_id,))
        return document

    def read(self, object_id: str):
        row = self.connection.execute("SELECT sealed FROM chunk_manifests WHERE object_id=?", (object_id,)).fetchone()
        if not row or row[0] != 1:
            raise StateConflict("object is not sealed")
        return self._decode(object_id)

    def _manifest(self, object_id: str) -> ChunkManifest:
        row = self.connection.execute("SELECT manifest FROM chunk_manifests WHERE object_id=?", (object_id,)).fetchone()
        if not row:
            raise StateConflict("unknown staged object")
        return ChunkManifest(**json.loads(row[0]))

    def _decode(self, object_id: str):
        manifest = self._manifest(object_id)
        rows = self.connection.execute("SELECT ordinal,content FROM object_chunks WHERE object_id=? ORDER BY ordinal", (object_id,)).fetchall()
        if tuple(row[0] for row in rows) != tuple(range(len(manifest.chunk_hashes))):
            raise StateConflict("snapshot chunks are incomplete")
        return decode_document(manifest, tuple(row[1] for row in rows))
