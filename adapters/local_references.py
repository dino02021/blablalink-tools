"""Local SQLite reference candidates and explicit CAS publication. No platform I/O."""

import hashlib
import json

from adapters.local_state import LocalStateStore, _transaction
from application.reference_catalog import read_reference
from application.tasks import StateConflict, validate_identifier
from domain.snapshots import canonical_json


class LocalReferences(LocalStateStore):
    def reference_head(self, scope_id: str) -> dict | None:
        validate_identifier(scope_id)
        row = self._connection.execute("SELECT * FROM reference_heads WHERE scope_id=?", (scope_id,)).fetchone()
        if row is None:
            return None
        document = self.get_document(row["reference_id"])
        read_reference(document)
        if document.envelope["scope_id"] != scope_id:
            raise StateConflict("reference head scope differs")
        return {**dict(row), "reference_version": document.envelope["reference_version"]}

    def create_reference_candidate(self, document, base_reference_id: str | None, code_sha: str) -> str:
        read_reference(document)
        validate_identifier(code_sha)
        scope = document.envelope["scope_id"]
        semantic = _hash({"scope": scope, "version": document.envelope["reference_version"], "base": base_reference_id, "code": code_sha})
        candidate = _hash({"document": document.snapshot_id, "base": base_reference_id, "code": code_sha})
        with _transaction(self._connection):
            prior = self._connection.execute("SELECT candidate_id FROM reference_candidates WHERE semantic_id=?", (semantic,)).fetchone()
            if prior:
                self.reference_candidate(prior[0])
                return prior[0]
            head = self.reference_head(scope)
            if (head["reference_id"] if head else None) != base_reference_id:
                raise StateConflict("reference candidate base changed")
            self._put_object(document.serialized)
            self._connection.execute("INSERT INTO reference_candidates VALUES(?,?,?,?,?,?,?)",
                (candidate, scope, document.snapshot_id, base_reference_id, code_sha, semantic, self._now()))
        return candidate

    def reference_candidate(self, candidate_id: str) -> dict:
        row = self._connection.execute("SELECT * FROM reference_candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
        if row is None:
            raise StateConflict("unknown reference candidate")
        document = self.get_document(row["document_id"])
        read_reference(document)
        if (document.envelope["scope_id"] != row["scope_id"] or candidate_id != _hash({"document": document.snapshot_id,
                "base": row["base_reference_id"], "code": row["validation_code_sha"]})
                or row["semantic_id"] != _hash({"scope": row["scope_id"], "version": document.envelope["reference_version"],
                    "base": row["base_reference_id"], "code": row["validation_code_sha"]})):
            raise StateConflict("reference candidate identity differs")
        return {**dict(row), "reference_version": document.envelope["reference_version"]}

    def publish_reference(self, request_id: str, candidate_id: str, confirmed_candidate_id: str,
                          expected_base_id: str | None, actor_id: str, code_sha: str) -> dict:
        for value in (request_id, candidate_id, confirmed_candidate_id, actor_id, code_sha):
            validate_identifier(value)
        with _transaction(self._connection):
            candidate = self.reference_candidate(candidate_id)
            if (candidate_id != confirmed_candidate_id or candidate["base_reference_id"] != expected_base_id
                    or candidate["validation_code_sha"] != code_sha):
                raise StateConflict("reference confirmation dependencies differ")
            body = {"candidate_id": candidate_id, "scope_id": candidate["scope_id"], "reference_id": candidate["document_id"],
                "base_reference_id": expected_base_id, "actor_id": actor_id, "code_sha": code_sha}
            publication_id = _hash(body)
            request = self._connection.execute("SELECT publication_id FROM reference_publication_requests WHERE request_id=?", (request_id,)).fetchone()
            if request and request[0] != publication_id:
                raise StateConflict("reference request already binds another intent")
            prior = self._connection.execute("SELECT publication_id FROM reference_publications WHERE candidate_id=?", (candidate_id,)).fetchone()
            if prior:
                if prior[0] != publication_id:
                    raise StateConflict("reference candidate already published another intent")
            else:
                head = self.reference_head(candidate["scope_id"])
                if (head["reference_id"] if head else None) != expected_base_id:
                    raise StateConflict("reference publication head changed")
                revision = (head["revision"] if head else 0)+1
                self._connection.execute("INSERT INTO reference_publications VALUES(?,?,?,?,?)",
                    (publication_id, candidate_id, canonical_json(body), revision, self._now()))
                self._connection.execute("INSERT INTO reference_heads VALUES(?,?,?) ON CONFLICT(scope_id) DO UPDATE SET reference_id=excluded.reference_id,revision=excluded.revision",
                    (candidate["scope_id"], candidate["document_id"], revision))
            self._connection.execute("INSERT OR IGNORE INTO reference_publication_requests VALUES(?,?)", (request_id, publication_id))
            return self.reference_publication(publication_id)

    def reference_publication(self, publication_id: str) -> dict:
        row = self._connection.execute("SELECT * FROM reference_publications WHERE publication_id=?", (publication_id,)).fetchone()
        if row is None:
            raise StateConflict("unknown reference publication")
        body = json.loads(row["signature"])
        if _hash(body) != publication_id or row["candidate_id"] != body["candidate_id"]:
            raise StateConflict("reference publication receipt differs")
        candidate = self.reference_candidate(body["candidate_id"])
        if candidate["document_id"] != body["reference_id"] or candidate["scope_id"] != body["scope_id"]:
            raise StateConflict("reference publication source differs")
        return {**body, "publication_id": publication_id, "revision": row["revision"], "published_at": row["published_at"]}


def _hash(value) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()
