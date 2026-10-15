"""Lease-fenced page evidence; opaque cursors/revisions never enter durable storage."""

import copy
import hashlib
import json
import re

from application.compute_budget import ComputeLimit
from application.tasks import StateConflict
from domain.execution import check_execution
from domain.snapshots import MemberKey, canonical_json, capture_document

STATUSES = frozenset({"complete", "partial", "failed", "auth_required"})


class CollectionCheckpoint:
    def __init__(self, store, lease, inputs, code_sha):
        self.store, self.lease, self.inputs = store, lease, inputs
        self.members = {MemberKey(**member) for member in inputs.payload["members"]}
        self.data = store.load_checkpoint(lease.job_id)
        if self.data is None:
            self.data = {"schema_version": 1, "checkpoint_kind": "training_collection", "collection_inputs_id": inputs.snapshot_id,
                "code_sha": code_sha, "sources": {}, "last_observation": None}
        if (set(self.data) != {"schema_version", "checkpoint_kind", "collection_inputs_id", "code_sha", "sources", "last_observation"}
                or type(self.data["schema_version"]) is not int or self.data["schema_version"] != 1
                or self.data["checkpoint_kind"] != "training_collection" or self.data["collection_inputs_id"] != inputs.snapshot_id
                or self.data["code_sha"] != code_sha or not isinstance(self.data["sources"], dict) or len(self.data["sources"]) > 900):
            raise StateConflict("collection checkpoint dependency differs")
        for key, source in self.data["sources"].items():
            try:
                area, member, kind = json.loads(key)
                if MemberKey(area, member) not in self.members or kind not in ("ownership", "details", "effects") or key != canonical_json([area, member, kind]):
                    raise ValueError()
                if (set(source) != {"request_scope_hash", "page_ids", "page_bytes", "status", "observed_at", "last_verification"}
                        or not _is_hash(source["request_scope_hash"]) or source["status"] not in STATUSES
                        or not isinstance(source["page_ids"], list) or not isinstance(source["page_bytes"], list)
                        or len(source["page_ids"]) != len(source["page_bytes"]) or len(source["page_ids"]) > 100
                        or any(not _is_hash(value) for value in source["page_ids"])
                        or any(type(size) is not int or not 1 <= size <= 1024*1024 for size in source["page_bytes"])):
                    raise ValueError()
            except (ValueError, TypeError, KeyError):
                raise StateConflict("collection checkpoint manifest invalid") from None
        self._check_bounds(self.data)
        self.latest_id = None

    def source(self, member, source, requested, reference, scope, roles, captured_at, max_rows):
        if (member not in self.members or scope != self.inputs.envelope["scope_id"]
                or reference != self.inputs.envelope["reference_version"] or tuple(roles) != tuple(self.inputs.payload["role_ids"])):
            raise StateConflict("collection checkpoint source dependency differs")
        return SourceCheckpoint(self, member, source, requested, captured_at, max_rows)

    def update_source(self, key, state):
        proposed = copy.deepcopy(self.data)
        proposed["sources"][key] = state
        check_execution()
        self._check_bounds(proposed)
        metadata = {key: value for key, value in proposed.items() if key != "sources"}
        self.latest_id = self.store.save_collection_progress(self.lease, metadata, key, state)
        self.data = proposed

    def record_observation(self, raw, training):
        proposed = copy.deepcopy(self.data)
        proposed["last_observation"] = {"raw_snapshot_id": raw.snapshot_id, "training_snapshot_id": training.snapshot_id,
            "captured_at": raw.envelope["captured_at"]}
        check_execution()
        self._check_bounds(proposed)
        self.latest_id = self.store.save_collection_observation(self.lease, proposed)
        self.data = proposed

    def _check_bounds(self, data):
        if (sum(sum(source["page_bytes"]) for source in data["sources"].values()) > 16*1024*1024
                or len(canonical_json(data).encode()) > 8*1024*1024):
            raise ComputeLimit("collection_checkpoint_capacity")

class SourceCheckpoint:
    def __init__(self, manager, member, source, requested, captured_at, max_rows):
        self.manager, self.member, self.source = manager, member, source
        self.requested, self.max_rows = tuple(requested), max_rows
        self.key = canonical_json([member.area_id, member.member_id, source])
        self.request_hash = _hash(list(requested))
        self.observed_at = capture_document("union_inputs", manager.inputs.envelope["scope_id"], captured_at,
            manager.inputs.envelope["reference_version"], {}).envelope["captured_at"]
        self.old = manager.data["sources"].get(self.key)
        self.page_ids, self.page_bytes = [], []
        self.was_reused = False

    def reuse_complete(self, first_evidence, first_options_complete):
        state = self.old
        if state is None or state["status"] != "complete" or state["request_scope_hash"] != self.request_hash or not state["page_ids"]:
            return None
        rows, evidence, pages, seen = [], [], [], set()
        revision, total, cursor_hash, options_complete = None, None, _hash(None), True
        allowed = set(self.manager.inputs.payload["role_ids"] if self.source == "ownership" else self.requested)
        for index, (page_id, size) in enumerate(zip(state["page_ids"], state["page_bytes"])):
            check_execution()
            document = self.manager.store.get_document(page_id)
            page = document.payload
            if (document.envelope["kind"] != "raw" or document.envelope["scope_id"] != self.manager.inputs.envelope["scope_id"]
                    or document.envelope["reference_version"] != self.manager.inputs.envelope["reference_version"]
                    or document.envelope["captured_at"] != state["observed_at"] or len(document.serialized.encode()) != size
                    or set(page) != {"fragment_kind", "schema_version", "member", "source", "request_scope_hash", "index", "evidence", "rows", "is_terminal", "is_options_complete"}
                    or page["fragment_kind"] != "training_source_page" or type(page["schema_version"]) is not int or page["schema_version"] != 1
                    or page["member"] != {"area_id": self.member.area_id, "member_id": self.member.member_id}
                    or page["source"] != self.source or page["request_scope_hash"] != self.request_hash or type(page["index"]) is not int or page["index"] != index
                    or type(page["is_terminal"]) is not bool or page["is_terminal"] != (index == len(state["page_ids"])-1)
                    or type(page["is_options_complete"]) is not bool or not isinstance(page["rows"], list)):
                raise StateConflict("collection cached page identity differs")
            proof = page["evidence"]
            if (not isinstance(proof, dict) or set(proof) != {"revision_hash", "request_cursor_hash", "next_cursor_hash", "total", "row_count", "rows_hash"}
                    or any(not _is_hash(proof[key]) for key in ("revision_hash", "request_cursor_hash", "next_cursor_hash", "rows_hash"))
                    or type(proof["row_count"]) is not int or proof["row_count"] != len(page["rows"])
                    or type(proof["total"]) is not int or not 0 <= proof["total"] <= self.max_rows
                    or proof["rows_hash"] != _hash(page["rows"]) or proof["request_cursor_hash"] != cursor_hash
                    or (proof["next_cursor_hash"] == _hash(None)) != page["is_terminal"] or not page["is_terminal"] and not page["rows"]):
                raise StateConflict("collection cached page proof differs")
            if index == 0:
                revision, total = proof["revision_hash"], proof["total"]
            if proof["revision_hash"] != revision or proof["total"] != total:
                raise StateConflict("collection cached source revision differs")
            ids = [row["id" if self.source == "effects" else "name_code"] for row in page["rows"]]
            if any(not isinstance(identity, str) or identity not in allowed or identity in seen for identity in ids) or len(set(ids)) != len(ids):
                raise StateConflict("collection cached source identity repeats")
            seen.update(ids)
            rows.extend(page["rows"])
            evidence.append(proof)
            pages.append(page)
            options_complete &= page["is_options_complete"]
            cursor_hash = proof["next_cursor_hash"]
        if (len(rows) != total or len(rows) > self.max_rows or cursor_hash != _hash(None)
                or self.source != "ownership" and seen != set(self.requested)):
            raise StateConflict("collection cached source coverage differs")
        if (first_evidence["revision_hash"] != revision or first_evidence["total"] != total
                or first_evidence["rows_hash"] != evidence[0]["rows_hash"] or first_evidence["row_count"] != evidence[0]["row_count"]
                or first_options_complete != pages[0]["is_options_complete"]
                or (first_evidence["next_cursor_hash"] == _hash(None)) != pages[0]["is_terminal"]):
            return None
        verification = {"protocol": "stable_revision_head_v1", "verified_at": self.observed_at,
            "cached_observed_at": state["observed_at"], "head_evidence": first_evidence}
        self.manager.update_source(self.key, {**state, "last_verification": verification})
        self.was_reused = True
        return {"status": "complete", "rows": tuple(rows), "evidence": tuple(evidence),
            "is_options_complete": options_complete, "cache_validation": verification}

    def record_page(self, cleaned, evidence, is_terminal, is_options_complete, is_complete):
        if len(self.page_ids) >= 100:
            raise ComputeLimit("collection_checkpoint_page_limit")
        document = capture_document("raw", self.manager.inputs.envelope["scope_id"], self.observed_at,
            self.manager.inputs.envelope["reference_version"], {"fragment_kind": "training_source_page", "schema_version": 1,
                "member": {"area_id": self.member.area_id, "member_id": self.member.member_id}, "source": self.source,
                "request_scope_hash": self.request_hash, "index": len(self.page_ids), "evidence": evidence, "rows": cleaned,
                "is_terminal": is_terminal, "is_options_complete": is_options_complete})
        size = len(document.serialized.encode())
        if size > 1024*1024:
            raise ComputeLimit("collection_checkpoint_page_capacity")
        page_id = self.manager.store.put_document(document)
        self.page_ids.append(page_id)
        self.page_bytes.append(size)
        self._record("complete" if is_complete else "partial")

    def finish(self, status):
        if not self.was_reused:
            self._record(status)

    def _record(self, status):
        state = {"request_scope_hash": self.request_hash, "page_ids": list(self.page_ids), "page_bytes": list(self.page_bytes),
            "status": status, "observed_at": self.observed_at, "last_verification": None}
        if self.manager.data["sources"].get(self.key) != state:
            self.manager.update_source(self.key, state)


def _hash(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _is_hash(value):
    return isinstance(value, str) and re.fullmatch("[a-f0-9]{64}", value) is not None
