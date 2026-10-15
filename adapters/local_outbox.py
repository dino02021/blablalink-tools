"""Local durable outbox; unknown sends never expire into automatic retries."""

import json
import math
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from adapters.local_state import LocalStateStore
from application.delivery import DeliveryClaim, DeliverySpec
from application.tasks import StateConflict, validate_identifier
from domain.snapshots import canonical_json


class LocalOutbox:
    def __init__(self, path: Path, clock=time.time):
        migration_store = LocalStateStore(path, clock)
        migration_store.close()
        self.clock = clock
        self.connection = sqlite3.connect(str(path), timeout=5, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")

    def close(self):
        self.connection.close()

    def enqueue(self, request_id: str, spec: DeliverySpec, confirmed_outbox_id: str) -> str:
        validate_identifier(request_id)
        if spec.outbox_id != confirmed_outbox_id:
            raise StateConflict("delivery intent is not confirmed")
        with _transaction(self.connection):
            existing = self.connection.execute("SELECT outbox_id FROM outbox_requests WHERE request_id=?", (request_id,)).fetchone()
            if existing and existing["outbox_id"] != spec.outbox_id:
                raise StateConflict("request cannot replace confirmed delivery")
            self.connection.execute("INSERT OR IGNORE INTO report_outbox VALUES(?,?,?,?)",
                (spec.outbox_id, request_id, spec.serialized, spec.outbox_id))
            self.spec(spec.outbox_id)
            self.connection.execute("INSERT OR IGNORE INTO outbox_requests VALUES(?,?)", (request_id, spec.outbox_id))
            self.connection.executemany("INSERT OR IGNORE INTO report_parts(outbox_id,ordinal,state) VALUES(?,?,'queued')",
                ((spec.outbox_id, index) for index in range(len(spec.parts))))
        return spec.outbox_id

    def spec(self, outbox_id: str) -> DeliverySpec:
        row = self.connection.execute("SELECT * FROM report_outbox WHERE outbox_id=?", (outbox_id,)).fetchone()
        if not row:
            raise StateConflict("unknown outbox")
        spec = DeliverySpec(**json.loads(row["spec_json"]))
        if spec.outbox_id != outbox_id or row["content_hash"] != outbox_id:
            raise StateConflict("outbox content corrupted")
        return spec

    def parts(self, outbox_id: str) -> tuple[dict, ...]:
        return tuple(dict(row) for row in self.connection.execute("SELECT * FROM report_parts WHERE outbox_id=? ORDER BY ordinal", (outbox_id,)))

    def claim(self, outbox_id: str, worker_id: str) -> DeliveryClaim | None:
        validate_identifier(worker_id)
        self.spec(outbox_id)
        with _transaction(self.connection):
            row = self.connection.execute("SELECT * FROM report_parts WHERE outbox_id=? AND state!='sent' ORDER BY ordinal LIMIT 1", (outbox_id,)).fetchone()
            if not row or row["state"] != "queued" or row["next_attempt_at"] > self.clock():
                return None
            self.connection.execute("UPDATE report_parts SET state='sending',attempt=attempt+1,generation=generation+1,worker_id=? WHERE outbox_id=? AND ordinal=?",
                (worker_id, outbox_id, row["ordinal"]))
            return DeliveryClaim(outbox_id, row["ordinal"], worker_id, row["generation"] + 1)

    def sent(self, claim: DeliveryClaim, message_id: str):
        validate_identifier(message_id)
        with _transaction(self.connection):
            self._require(claim, "sending")
            self.connection.execute("UPDATE report_parts SET state='sent',message_id=?,sent_at=?,error_code=NULL WHERE outbox_id=? AND ordinal=?",
                (message_id, self.clock(), claim.outbox_id, claim.ordinal))

    def reject(self, claim: DeliveryClaim, retry_after: float = 0):
        if type(retry_after) not in (int, float) or not math.isfinite(retry_after) or not 0 <= retry_after <= 86400:
            raise ValueError("invalid retry delay")
        with _transaction(self.connection):
            row = self._require(claim, "sending")
            state = "failed" if row["attempt"] >= 3 else "queued"
            self.connection.execute("UPDATE report_parts SET state=?,next_attempt_at=?,error_code='provider_not_sent' WHERE outbox_id=? AND ordinal=?",
                (state, self.clock() + retry_after, claim.outbox_id, claim.ordinal))

    def uncertain(self, claim: DeliveryClaim):
        with _transaction(self.connection):
            self._require(claim, "sending")
            self.connection.execute("UPDATE report_parts SET state='delivery_uncertain',error_code='delivery_unverified' WHERE outbox_id=? AND ordinal=?",
                (claim.outbox_id, claim.ordinal))

    def recover_interrupted(self, outbox_id: str):
        """Quarantine a vanished invocation; this grants no retry authority."""
        with _transaction(self.connection):
            self.connection.execute("UPDATE report_parts SET state='delivery_uncertain',error_code='sender_interrupted' WHERE outbox_id=? AND state='sending'", (outbox_id,))

    def reconcile(self, claim: DeliveryClaim, is_writer_stopped: bool, message_id: str | None,
                  observed_payload: dict | None = None, is_absence_verified: bool = False,
                  observed_target: str | None = None):
        if is_writer_stopped is not True:
            raise StateConflict("old sender termination must be established")
        spec = self.spec(claim.outbox_id)
        with _transaction(self.connection):
            row = self._require(claim, "delivery_uncertain")
            if message_id is not None:
                validate_identifier(message_id)
                if (observed_payload is None or observed_target != spec.target_thread
                        or canonical_json(spec.payload(claim.ordinal)) != canonical_json(observed_payload)):
                    raise StateConflict("message receipt differs from fixed payload")
                self.connection.execute("UPDATE report_parts SET state='sent',message_id=?,sent_at=?,error_code=NULL WHERE outbox_id=? AND ordinal=?",
                    (message_id, self.clock(), claim.outbox_id, claim.ordinal))
            elif is_absence_verified is True:
                self.connection.execute("UPDATE report_parts SET state=?,generation=generation+1,error_code='verified_absent' WHERE outbox_id=? AND ordinal=?",
                    ("failed" if row["attempt"] >= 3 else "queued", claim.outbox_id, claim.ordinal))
            else:
                raise StateConflict("unknown outcome cannot be retried without evidence")

    def _require(self, claim, state):
        row = self.connection.execute("SELECT * FROM report_parts WHERE outbox_id=? AND ordinal=?", (claim.outbox_id, claim.ordinal)).fetchone()
        if not row or row["state"] != state or row["worker_id"] != claim.worker_id or row["generation"] != claim.generation:
            raise StateConflict("sender claim is no longer valid")
        return row


@contextmanager
def _transaction(connection):
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
