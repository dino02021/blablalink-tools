"""Mock sender port with durable route/grant checks and non-expiring uncertain holds."""

import time
from dataclasses import asdict
import hashlib
import math

from adapters.local_outbox import LocalOutbox, _transaction
from adapters.local_routing_registry import LocalRoutingRegistry
from adapters.route_leases import release_resolved_route_leases
from application.delivery import DeliveryClaim
from application.routing_registry import SenderGrant
from application.tasks import StateConflict, validate_identifier
from domain.snapshots import canonical_json


class LocalAuthorizedOutbox(LocalOutbox):
    def __init__(self, path, principal_id, clock=time.time):
        validate_identifier(principal_id)
        super().__init__(path, clock)
        self.principal_id = principal_id
        try:
            self.registry = LocalRoutingRegistry(path, clock)
        except BaseException:
            super().close()
            raise

    def close(self):
        self.registry.close()
        super().close()

    def enqueue(self, *args, **kwargs):
        raise StateConflict("authorized sender requires a scoped confirmed batch")

    def confirm_batch(self, request_id, candidate_id, confirmed_candidate_id, validator=None):
        validate_identifier(request_id)
        if validator is not None or candidate_id != confirmed_candidate_id:
            raise StateConflict("authorized confirmation uses its fixed internal validator")
        with _transaction(self.connection):
            candidate = self.batch(candidate_id)
            request = self.connection.execute("SELECT candidate_id FROM report_batch_requests WHERE request_id=?", (request_id,)).fetchone()
            if request and request[0] != candidate_id:
                raise StateConflict("batch request cannot change its candidate")
            accepted = self.connection.execute("SELECT is_accepted FROM report_send_candidates WHERE candidate_id=?", (candidate_id,)).fetchone()[0]
            if accepted:
                self._batch_binding(candidate_id)
            else:
                preview = self.registry.preview_report_authorization(candidate, self.principal_id)
                self.connection.execute("INSERT INTO authorized_report_batches VALUES(?,?,?,?,?,?)",
                    (candidate_id, candidate.scope_id, self.principal_id, candidate.route_snapshot_id, preview["grant_id"], preview["grant_revision"]))
                for index, spec in enumerate(candidate.deliveries):
                    if self.connection.execute("SELECT 1 FROM report_outbox WHERE outbox_id=?", (spec.outbox_id,)).fetchone():
                        raise StateConflict("legacy outbox cannot acquire sender authority retroactively")
                    self._enqueue(f"batch:{candidate_id}:{index}", spec)
                    self.connection.execute("INSERT INTO authorized_report_deliveries VALUES(?,?,?)", (spec.outbox_id, candidate_id, _semantic(spec)))
                self.connection.execute("UPDATE report_send_candidates SET is_accepted=1 WHERE candidate_id=?", (candidate_id,))
            self.connection.execute("INSERT OR IGNORE INTO report_batch_requests VALUES(?,?)", (request_id, candidate_id))
            return tuple(spec.outbox_id for spec in candidate.deliveries)

    def claim(self, outbox_id, worker_id):
        validate_identifier(worker_id)
        with _transaction(self.connection):
            spec, binding = self._delivery_binding(outbox_id)
            release_resolved_route_leases(self.connection, binding["scope_id"])
            row = self.connection.execute("SELECT * FROM report_parts WHERE outbox_id=? AND state!='sent' ORDER BY ordinal LIMIT 1", (outbox_id,)).fetchone()
            if row is None or row["state"] != "queued" or row["next_attempt_at"] > self.clock():
                return None
            if self.connection.execute("SELECT 1 FROM authorized_route_leases l JOIN authorized_report_deliveries d ON d.outbox_id=l.outbox_id WHERE d.semantic_id=? AND l.outbox_id!=? LIMIT 1",
                    (binding["semantic_id"], outbox_id)).fetchone():
                raise StateConflict("same report delivery has an unresolved prior intent")
            preview = self.registry.preview_delivery_route(binding["scope_id"], binding["route_snapshot_id"], (spec,), self.principal_id)
            if self.connection.execute("SELECT 1 FROM authorized_route_leases WHERE outbox_id=? AND ordinal=?", (outbox_id, row["ordinal"])).fetchone():
                raise StateConflict("prior sender route outcome remains unresolved")
            generation = row["generation"]+1
            now = self._now()
            self.connection.execute("INSERT INTO authorized_route_leases VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (outbox_id, row["ordinal"], generation, binding["scope_id"], self.principal_id, binding["route_snapshot_id"], preview["grant_id"], preview["grant_revision"], 0, now+30, None))
            self.connection.execute("UPDATE report_parts SET state='sending',attempt=attempt+1,generation=?,worker_id=? WHERE outbox_id=? AND ordinal=?",
                (generation, worker_id, outbox_id, row["ordinal"]))
            return DeliveryClaim(outbox_id, row["ordinal"], worker_id, generation)

    def before_send(self, claim):
        with _transaction(self.connection):
            spec, binding = self._delivery_binding(claim.outbox_id)
            row = self._require(claim, "sending")
            lease = self.connection.execute("SELECT * FROM authorized_route_leases WHERE outbox_id=? AND ordinal=?", (claim.outbox_id, claim.ordinal)).fetchone()
            if lease is None or lease["generation"] != claim.generation or lease["is_started"] != 0:
                raise StateConflict("sender start permit is absent or already consumed")
            now = self._now()
            is_allowed = lease["start_deadline"]-30 <= now <= lease["start_deadline"]
            try:
                self.registry.preview_delivery_route(binding["scope_id"], binding["route_snapshot_id"], (spec,), self.principal_id)
            except StateConflict:
                is_allowed = False
            if not is_allowed:
                # An unused permit proves this attempt has not invoked the provider.
                self.connection.execute("UPDATE report_parts SET state=?,generation=generation+1,next_attempt_at=?,error_code='sender_not_started' WHERE outbox_id=? AND ordinal=?",
                    ("failed" if row["attempt"] >= 3 else "queued", now, claim.outbox_id, claim.ordinal))
                self.connection.execute("DELETE FROM authorized_route_leases WHERE outbox_id=? AND ordinal=?", (claim.outbox_id, claim.ordinal))
                return False
            self.connection.execute("UPDATE authorized_route_leases SET is_started=1,started_at=? WHERE outbox_id=? AND ordinal=?", (now, claim.outbox_id, claim.ordinal))
            return True

    def sent(self, claim, message_id):
        _, binding = self._delivery_binding(claim.outbox_id)
        self._require_started(claim)
        super().sent(claim, message_id)
        self._release_closed(binding["scope_id"])

    def reject(self, claim, retry_after=0):
        _, binding = self._delivery_binding(claim.outbox_id)
        self._require_started(claim)
        super().reject(claim, retry_after)
        self._release_closed(binding["scope_id"])

    def uncertain(self, claim):
        self._delivery_binding(claim.outbox_id)
        super().uncertain(claim)

    def recover_interrupted(self, outbox_id):
        self._delivery_binding(outbox_id)
        super().recover_interrupted(outbox_id)

    def reconcile(self, claim, *args, **kwargs):
        _, binding = self._delivery_binding(claim.outbox_id)
        super().reconcile(claim, *args, **kwargs)
        self._release_closed(binding["scope_id"])

    def _batch_binding(self, candidate_id):
        candidate = self.batch(candidate_id)
        row = self.connection.execute("SELECT * FROM authorized_report_batches WHERE candidate_id=?", (candidate_id,)).fetchone()
        accepted = self.connection.execute("SELECT is_accepted FROM report_send_candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
        if (row is None or row["principal_id"] != self.principal_id or row["scope_id"] != candidate.scope_id
                or row["route_snapshot_id"] != candidate.route_snapshot_id or accepted is None or accepted[0] != 1):
            raise StateConflict("batch is not authorized for this sender")
        grant = SenderGrant(**self.registry.get_result(row["grant_id"]))
        if (grant.grant_id != row["grant_id"] or grant.scope_id != candidate.scope_id or grant.principal_id != self.principal_id
                or not grant.is_enabled or any(delivery.target_thread not in grant.allowed_threads
                    or set(delivery.mention_user_ids) - set(grant.allowed_mentions) for delivery in candidate.deliveries)):
            raise StateConflict("batch confirmation grant proof differs")
        return dict(row)

    def _delivery_binding(self, outbox_id):
        spec = self.spec(outbox_id)
        row = self.connection.execute("SELECT candidate_id,semantic_id FROM authorized_report_deliveries WHERE outbox_id=?", (outbox_id,)).fetchone()
        if row is None:
            raise StateConflict("delivery has no scoped sender binding")
        binding = self._batch_binding(row[0])
        candidate = self.batch(row[0])
        if row["semantic_id"] != _semantic(spec) or not any(delivery.serialized == spec.serialized for delivery in candidate.deliveries):
            raise StateConflict("delivery differs from its authorized candidate")
        return spec, {**binding, "semantic_id": row["semantic_id"]}

    def _release_closed(self, scope_id):
        with _transaction(self.connection):
            release_resolved_route_leases(self.connection, scope_id)

    def _require_started(self, claim):
        row = self.connection.execute("SELECT generation,is_started FROM authorized_route_leases WHERE outbox_id=? AND ordinal=?", (claim.outbox_id, claim.ordinal)).fetchone()
        if row is None or row["generation"] != claim.generation or row["is_started"] != 1:
            raise StateConflict("provider attempt has no consumed start permit")

    def _now(self):
        now = self.clock()
        if type(now) not in (int, float) or not math.isfinite(now) or not 0 <= now <= 2**53:
            raise ValueError("trusted sender time unavailable")
        return float(now)


def _semantic(spec):
    fields = asdict(spec)
    fields.pop("intent_id")
    return hashlib.sha256(canonical_json(fields).encode()).hexdigest()
