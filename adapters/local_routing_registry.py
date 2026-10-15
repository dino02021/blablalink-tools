"""Local route heads and sender allowlists; preview is not authority to send HTTP."""

from dataclasses import asdict
import hashlib
import json

from adapters.local_state import LocalStateStore, _transaction
from adapters.route_leases import release_resolved_route_leases
from application.report_delivery import ReportCandidateValidator
from application.routing_registry import RoutingAdministrator, SenderGrant, validate_route_snapshot
from application.tasks import ResourceBusy, StateConflict, validate_identifier
from domain.snapshots import canonical_json


class LocalRoutingRegistry(LocalStateStore):
    def route_head(self, scope_id):
        row = self._connection.execute("SELECT * FROM route_registry_heads WHERE scope_id=?", (scope_id,)).fetchone()
        if row is None:
            return None
        document = self.get_document(row["route_snapshot_id"])
        validate_route_snapshot(document)
        if document.envelope["scope_id"] != scope_id:
            raise StateConflict("route head scope differs")
        return dict(row)

    def sender_grant(self, scope_id, principal_id):
        row = self._connection.execute("SELECT * FROM sender_grants WHERE scope_id=? AND principal_id=?", (scope_id, principal_id)).fetchone()
        if row is None:
            return None
        grant = SenderGrant(**self.get_result(row["grant_id"]))
        if grant.grant_id != row["grant_id"] or grant.scope_id != scope_id or grant.principal_id != principal_id:
            raise StateConflict("sender grant content differs")
        return grant, row["revision"]

    def publish_route(self, request_id, document, confirmed_route_id, expected_base_id, administrator, code_sha):
        validate_route_snapshot(document)
        scope = document.envelope["scope_id"]
        _require_admin(administrator, scope)
        validate_identifier(request_id)
        validate_identifier(code_sha)
        if document.snapshot_id != confirmed_route_id:
            raise StateConflict("route confirmation differs")
        body = {"change_kind": "route", "scope_id": scope, "route_snapshot_id": document.snapshot_id,
            "expected_base_id": expected_base_id, "actor_id": administrator.actor_id, "code_sha": code_sha}
        with _transaction(self._connection):
            prior = self._prior_change(request_id, body)
            if prior is not None:
                return prior
            head = self.route_head(scope)
            if (head["route_snapshot_id"] if head else None) != expected_base_id:
                raise StateConflict("route head changed")
            release_resolved_route_leases(self._connection, scope)
            if self._connection.execute("SELECT 1 FROM authorized_route_leases WHERE scope_id=? LIMIT 1", (scope,)).fetchone():
                raise ResourceBusy("unresolved sender owns the route")
            self._put_object(document.serialized)
            revision = (head["revision"] if head else 0)+1
            self._connection.execute("INSERT INTO route_registry_heads VALUES(?,?,?) ON CONFLICT(scope_id) DO UPDATE SET route_snapshot_id=excluded.route_snapshot_id,revision=excluded.revision",
                (scope, document.snapshot_id, revision))
            return self._record_change(request_id, body, revision)

    def publish_sender_grant(self, request_id, grant, confirmed_grant_id, expected_revision, administrator, code_sha):
        if not isinstance(grant, SenderGrant):
            raise ValueError("validated sender grant required")
        _require_admin(administrator, grant.scope_id)
        validate_identifier(request_id)
        validate_identifier(code_sha)
        if type(expected_revision) is not int or expected_revision < 0 or grant.grant_id != confirmed_grant_id:
            raise StateConflict("sender grant confirmation differs")
        body = {"change_kind": "sender_grant", "scope_id": grant.scope_id, "principal_id": grant.principal_id,
            "grant_id": grant.grant_id, "expected_revision": expected_revision, "actor_id": administrator.actor_id, "code_sha": code_sha}
        with _transaction(self._connection):
            prior = self._prior_change(request_id, body)
            if prior is not None:
                return prior
            current = self.sender_grant(grant.scope_id, grant.principal_id)
            if (current[1] if current else 0) != expected_revision:
                raise StateConflict("sender grant revision changed")
            self._put_object(canonical_json(asdict(grant)))
            self._connection.execute("INSERT INTO sender_grants VALUES(?,?,?,?) ON CONFLICT(scope_id,principal_id) DO UPDATE SET grant_id=excluded.grant_id,revision=excluded.revision",
                (grant.scope_id, grant.principal_id, grant.grant_id, expected_revision+1))
            return self._record_change(request_id, body, expected_revision+1)

    def preview_report_authorization(self, candidate, principal_id):
        ReportCandidateValidator(self)(candidate)
        return {**self.preview_delivery_route(candidate.scope_id, candidate.route_snapshot_id, candidate.deliveries, principal_id),
            "candidate_id": candidate.candidate_id}

    def preview_delivery_route(self, scope_id, route_snapshot_id, deliveries, principal_id):
        validate_identifier(principal_id)
        head = self.route_head(scope_id)
        current = self.sender_grant(scope_id, principal_id)
        if head is None or head["route_snapshot_id"] != route_snapshot_id or current is None:
            raise StateConflict("report route or sender grant is not current")
        grant, revision = current
        if not deliveries or not grant.is_enabled or any(delivery.route_version != route_snapshot_id or delivery.target_thread not in grant.allowed_threads
                or set(delivery.mention_user_ids) - set(grant.allowed_mentions) for delivery in deliveries):
            raise StateConflict("report destination or mention is not authorized")
        return {"is_preview_only": True, "scope_id": scope_id,
            "principal_id": principal_id, "route_snapshot_id": route_snapshot_id,
            "grant_id": grant.grant_id, "grant_revision": revision}

    def routing_change(self, change_id):
        row = self._connection.execute("SELECT * FROM routing_changes WHERE change_id=?", (change_id,)).fetchone()
        if row is None:
            raise StateConflict("unknown routing change")
        body = json.loads(row["signature"])
        if _hash(body) != change_id:
            raise StateConflict("routing change receipt differs")
        return {**body, "change_id": change_id, "revision": row["revision"], "created_at": row["created_at"]}

    def _prior_change(self, request_id, body):
        change = _hash(body)
        request = self._connection.execute("SELECT change_id FROM routing_change_requests WHERE request_id=?", (request_id,)).fetchone()
        if request and request[0] != change:
            raise StateConflict("routing request cannot replace its intent")
        prior = self._connection.execute("SELECT change_id FROM routing_changes WHERE change_id=?", (change,)).fetchone()
        if prior:
            self._connection.execute("INSERT OR IGNORE INTO routing_change_requests VALUES(?,?)", (request_id, change))
            return self.routing_change(change)
        return None

    def _record_change(self, request_id, body, revision):
        change = _hash(body)
        self._connection.execute("INSERT INTO routing_changes VALUES(?,?,?,?)", (change, canonical_json(body), revision, self._now()))
        self._connection.execute("INSERT INTO routing_change_requests VALUES(?,?)", (request_id, change))
        return self.routing_change(change)


def _require_admin(administrator, scope):
    if not isinstance(administrator, RoutingAdministrator) or scope not in administrator.scope_ids:
        raise StateConflict("trusted scoped routing administrator required")


def _hash(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()
