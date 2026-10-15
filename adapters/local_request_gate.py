"""Persistent local signature replay gate; no listener or remote secret store."""

import hashlib
import time

from adapters.local_raid_plans import LocalRaidPlans
from adapters.local_state import _transaction
from application.control_requests import RequestCapacity, RequestDenied, resolve_request_workbooks, verify_request
from application.tasks import StateConflict
from domain.snapshots import canonical_json


class LocalRequestGate(LocalRaidPlans):
    def __init__(self, path, principals, clock=time.time, principal_limit=60, global_limit=10000):
        if any(type(value) is not int or not 1 <= value <= 100000 for value in (principal_limit, global_limit)):
            raise ValueError("bounded control capacity required")
        super().__init__(path, clock)
        self.principal_limit, self.global_limit = principal_limit, global_limit
        self.principals = {principal.principal_id: principal for principal in principals}
        if len(self.principals) != len(principals):
            self.close()
            raise ValueError("duplicate request principal")

    def submit_signed(self, envelope: dict, body: bytes):
        principal_id = envelope.get("principal_id") if isinstance(envelope, dict) else None
        principal = self.principals.get(principal_id) if isinstance(principal_id, str) else None
        if principal is None:
            raise RequestDenied("unknown_principal")
        observed_at = self._clock()
        task = verify_request(envelope, body, principal, observed_at)
        now = int(observed_at)
        try:
            workbooks = resolve_request_workbooks(self, task)
        except (KeyError, ValueError, TypeError, StateConflict):
            raise RequestDenied("input_scope_denied") from None
        if not workbooks <= principal.workbooks:
            raise RequestDenied("workbook_denied")
        envelope_hash = hashlib.sha256(canonical_json(envelope).encode()).hexdigest()
        request_key = "signed:" + hashlib.sha256(canonical_json([principal.principal_id, envelope["request_id"]]).encode()).hexdigest()
        with _transaction(self._connection):
            watermark = self._connection.execute("SELECT observed_at FROM control_clock_watermark WHERE singleton=1").fetchone()
            if watermark and now < watermark[0]:
                raise OSError("control clock moved backwards")
            nonce = self._connection.execute("SELECT envelope_hash FROM control_nonces WHERE principal_id=? AND key_id=? AND nonce=?",
                (principal.principal_id, envelope["key_id"], envelope["nonce"])).fetchone()
            if nonce and nonce[0] != envelope_hash:
                raise RequestDenied("nonce_reused")
            request = self._connection.execute("SELECT body_hash FROM control_requests WHERE principal_id=? AND request_id=?",
                (principal.principal_id, envelope["request_id"])).fetchone()
            if request and request[0] != envelope["body_sha256"]:
                raise RequestDenied("request_changed")
            if nonce is None:
                self._reserve_control_capacity(principal.principal_id, now)
            self._connection.execute("INSERT OR IGNORE INTO control_nonces(principal_id,key_id,nonce,envelope_hash,signed_at) VALUES(?,?,?,?,?)",
                (principal.principal_id, envelope["key_id"], envelope["nonce"], envelope_hash, envelope["timestamp"]))
            self._connection.execute("INSERT OR IGNORE INTO control_requests(principal_id,request_id,body_hash,request_key) VALUES(?,?,?,?)",
                (principal.principal_id, envelope["request_id"], envelope["body_sha256"], request_key))
            self._connection.execute("INSERT INTO control_clock_watermark VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET observed_at=excluded.observed_at", (now,))
            self._connection.execute("DELETE FROM control_nonces WHERE signed_at<?", (now - 300,))
            self._connection.execute("DELETE FROM control_request_usage WHERE (bucket_type='principal_minute' AND slot<?) OR (bucket_type='global_day' AND slot<?)",
                (now // 60, now // 86400))
        # Crash after replay recording is safe: retry reuses this stable coordinator request key.
        # Do not acknowledge a job until submit_job itself has durably committed.
        return self.submit_job(request_key, task)

    def _reserve_control_capacity(self, principal_id, now):
        buckets = (("principal_minute", principal_id, now // 60, self.principal_limit, 60 - now % 60),
                   ("global_day", "all", now // 86400, self.global_limit, 86400 - now % 86400))
        for kind, key, slot, limit, retry in buckets:
            used = self._connection.execute("SELECT used FROM control_request_usage WHERE bucket_type=? AND bucket_key=? AND slot=?", (kind, key, slot)).fetchone()
            if used and used[0] >= limit:
                raise RequestCapacity(retry)
            self._connection.execute("INSERT INTO control_request_usage VALUES(?,?,?,1) ON CONFLICT(bucket_type,bucket_key,slot) DO UPDATE SET used=used+1", (kind, key, slot))
