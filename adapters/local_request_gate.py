"""Persistent local signature replay gate; no listener or remote secret store."""

import hashlib
import time

from adapters.local_raid_plans import LocalRaidPlans
from adapters.local_state import _transaction
from application.control_requests import RequestDenied, resolve_request_workbooks, verify_request
from application.tasks import StateConflict
from domain.snapshots import canonical_json


class LocalRequestGate(LocalRaidPlans):
    def __init__(self, path, principals, clock=time.time):
        super().__init__(path, clock)
        self.principals = {principal.principal_id: principal for principal in principals}
        if len(self.principals) != len(principals):
            self.close()
            raise ValueError("duplicate request principal")

    def submit_signed(self, envelope: dict, body: bytes):
        principal_id = envelope.get("principal_id") if isinstance(envelope, dict) else None
        principal = self.principals.get(principal_id) if isinstance(principal_id, str) else None
        if principal is None:
            raise RequestDenied("unknown_principal")
        task = verify_request(envelope, body, principal, self._clock())
        try:
            workbooks = resolve_request_workbooks(self, task)
        except (KeyError, ValueError, TypeError, StateConflict):
            raise RequestDenied("input_scope_denied") from None
        if not workbooks <= principal.workbooks:
            raise RequestDenied("workbook_denied")
        envelope_hash = hashlib.sha256(canonical_json(envelope).encode()).hexdigest()
        request_key = "signed:" + hashlib.sha256(canonical_json([principal.principal_id, envelope["request_id"]]).encode()).hexdigest()
        with _transaction(self._connection):
            nonce = self._connection.execute("SELECT envelope_hash FROM control_nonces WHERE principal_id=? AND key_id=? AND nonce=?",
                (principal.principal_id, envelope["key_id"], envelope["nonce"])).fetchone()
            if nonce and nonce[0] != envelope_hash:
                raise RequestDenied("nonce_reused")
            request = self._connection.execute("SELECT body_hash FROM control_requests WHERE principal_id=? AND request_id=?",
                (principal.principal_id, envelope["request_id"])).fetchone()
            if request and request[0] != envelope["body_sha256"]:
                raise RequestDenied("request_changed")
            self._connection.execute("INSERT OR IGNORE INTO control_nonces VALUES(?,?,?,?)",
                (principal.principal_id, envelope["key_id"], envelope["nonce"], envelope_hash))
            self._connection.execute("INSERT OR IGNORE INTO control_requests(principal_id,request_id,body_hash,request_key) VALUES(?,?,?,?)",
                (principal.principal_id, envelope["request_id"], envelope["body_sha256"], request_key))
        # Crash after replay recording is safe: retry reuses this stable coordinator request key.
        # Do not acknowledge a job until submit_job itself has durably committed.
        return self.submit_job(request_key, task)
