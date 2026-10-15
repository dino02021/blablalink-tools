"""Offline collector protocol with bounded page evidence; no HTTP or credential loader."""

from dataclasses import dataclass
import hashlib
from typing import Protocol

from application.compute_budget import ComputeExecutionCheck, ComputeLimit
from application.collection_checkpoint import CollectionCheckpoint
from application.tasks import StateConflict
from domain.execution import check_execution, execution_checks
from domain.normalization import capture_raw_training, normalize_training
from domain.snapshots import MemberKey, canonical_json, capture_document

SOURCES = ("ownership", "details", "effects")


class CollectionSourceError(RuntimeError):
    def __init__(self, is_auth_required=False):
        super().__init__("collection_source_unavailable")
        self.is_auth_required = is_auth_required is True


class CollectionPort(Protocol):
    def fetch_page(self, member: MemberKey, source: str, cursor: str | None,
                   requested_ids: tuple[str, ...]) -> dict: ...


@dataclass(frozen=True)
class SourceCollection:
    status: str
    rows: tuple[dict, ...]
    evidence: tuple[dict, ...]
    is_options_complete: bool = False
    cache_validation: dict | None = None


def collect_training(port: CollectionPort, scope_id: str, captured_at: str, reference_version: str,
                     roster: tuple[MemberKey, ...], role_ids: tuple[str, ...],
                     max_pages: int = 100, max_rows: int = 10000, checkpoint=None):
    if (not roster or len(roster) > 300 or len(set(roster)) != len(roster) or any(not isinstance(m, MemberKey) for m in roster)
            or not role_ids or len(role_ids) > 1000 or len(set(role_ids)) != len(role_ids)
            or any(not isinstance(r, str) or not r.strip() for r in role_ids)):
        raise ValueError("fixed nonempty unique roster and role reference required")
    if any(type(v) is not int or not 1 <= v <= 100000 for v in (max_pages, max_rows)):
        raise ValueError("bounded collection limits required")
    members, proof = [], []
    for member in roster:
        check_execution()
        ownership = _collect_source(port, member, "ownership", (), reference_version, scope_id,
                                    role_ids, captured_at, max_pages, max_rows, checkpoint)
        owned_ids = tuple(row["name_code"] for row in ownership.rows)
        details = (SourceCollection("auth_required", (), ()) if ownership.status == "auth_required" else
            _collect_source(port, member, "details", owned_ids, reference_version, scope_id,
                            role_ids, captured_at, max_pages, max_rows, checkpoint))
        option_ids = tuple(sorted({str(value) for row in details.rows for key, value in row.items()
                                  if key.startswith("equip_option") and value not in (None, "", 0, "0")}))
        effects = (SourceCollection("auth_required", (), ()) if details.status == "auth_required" else
            _collect_source(port, member, "effects", option_ids, reference_version, scope_id,
                            role_ids, captured_at, max_pages, max_rows, checkpoint))
        detail_status = "auth_required" if "auth_required" in (details.status, effects.status) else details.status
        members.append({"area_id": member.area_id, "member_id": member.member_id,
            "ownership": {"status": ownership.status, "characters": list(ownership.rows)},
            "details": {"status": detail_status, "requested_codes": list(owned_ids),
                "character_details": [] if detail_status == "auth_required" else list(details.rows),
                "is_options_complete": details.is_options_complete and effects.status == "complete",
                "state_effects": list(effects.rows)}})
        proof.append({"member": {"area_id": member.area_id, "member_id": member.member_id},
            "sources": {name: {"status": result.status, "pages": list(result.evidence),
                **({"cache_validation": result.cache_validation} if result.cache_validation is not None else {})}
                        for name, result in zip(SOURCES, (ownership, details, effects))}})
    raw = capture_raw_training(scope_id, captured_at, reference_version, tuple(members), role_ids)
    return capture_document("raw", scope_id, captured_at, reference_version,
        {**raw.payload, "collection_protocol_version": 1, "collection_proof": proof})


def run_collection_job(store, lease, port: CollectionPort, captured_at: str, budget):
    try:
        with execution_checks(ComputeExecutionCheck(store, lease, budget)):
            check_execution()
            job = store.get_job(lease.job_id)
            if job.spec.command != "refresh_roster_and_training":
                raise StateConflict("not a training collection job")
            inputs = store.get_document(job.spec.version_map["collection_inputs"])
            if (inputs.envelope["kind"] != "union_inputs" or inputs.envelope["scope_id"] != job.spec.scope_id
                    or inputs.payload.get("collection_kind") != "training_inputs"):
                raise StateConflict("collection inputs differ from job scope")
            payload = inputs.payload
            checkpoint = CollectionCheckpoint(store, lease, inputs, job.spec.code_sha)
            raw = collect_training(port, job.spec.scope_id, captured_at, inputs.envelope["reference_version"],
                tuple(MemberKey(**member) for member in payload["members"]), tuple(payload["role_ids"]), checkpoint=checkpoint)
            normalized = normalize_training(raw, tuple(payload["role_ids"]), payload["function_prefixes"])
            check_execution()
            raw_id = store.put_document(raw)
            training_id = store.put_document(normalized.training)
            checkpoint.record_observation(raw, normalized.training)
            if normalized.is_auth_required:
                return store.defer(lease, "auth_required", "collection_auth_required")
            statuses = [source["status"] for member in raw.payload["collection_proof"] for source in member["sources"].values()]
            if any(status != "complete" for status in statuses) or normalized.issues or normalized.missing_details:
                return store.defer(lease, "retry_wait", "collection_incomplete", delay_seconds=5)
            check_execution()
            return store.complete_compute(lease, {"raw_snapshot_id": raw_id, "training_snapshot_id": training_id,
                "collection_inputs_id": inputs.snapshot_id, "collection_protocol_version": 1,
                "collection_checkpoint_id": checkpoint.latest_id}, is_publication_required=False)
    except ComputeLimit as error:
        return store.defer(lease, "capacity_blocked", error.code)


def _collect_source(port, member, source, requested, reference, scope, roles, captured_at, max_pages, max_rows, checkpoint=None):
    observer = checkpoint.source(member, source, requested, reference, scope, roles, captured_at, max_rows) if checkpoint is not None else None
    rows, evidence, seen_ids, seen_cursors = [], [], set(), {None}
    cursor, revision, total = None, None, None
    is_options_complete = True
    try:
        for _ in range(max_pages):
            check_execution()
            page = port.fetch_page(member, source, cursor, requested)
            if (page["member"] != {"area_id": member.area_id, "member_id": member.member_id}
                    or page["source"] != source or page["scope_id"] != scope or page["reference_version"] != reference):
                raise ValueError("page dependency mismatch")
            page_revision, page_total = page["source_revision"], page["total"]
            next_cursor = page["next_cursor"]
            if (not isinstance(page_revision, str) or not page_revision or len(page_revision) > 512
                    or type(page_total) is not int or not 0 <= page_total <= max_rows
                    or next_cursor is not None and (not isinstance(next_cursor, str) or not next_cursor or len(next_cursor) > 2048)):
                raise ValueError("invalid page metadata")
            if revision is None:
                revision, total = page_revision, page_total
            if revision != page_revision or total != page_total:
                raise ValueError("source revision changed across pages")
            if not isinstance(page["rows"], list) or len(page["rows"]) > max_rows - len(rows):
                raise ValueError("page exceeds row budget")
            cleaned = _clean_rows(source, page["rows"], member, scope, captured_at, reference)
            key = "id" if source == "effects" else "name_code"
            allowed = set(roles if source == "ownership" else requested)
            ids = [row[key] for row in cleaned]
            if (any(not isinstance(identity, str) or identity not in allowed for identity in ids)
                    or len(set(ids)) != len(ids) or seen_ids.intersection(ids)
                    or len(rows) + len(cleaned) > max_rows):
                raise ValueError("duplicate or foreign page identity")
            seen_ids.update(ids)
            rows.extend(cleaned)
            is_options_complete &= page.get("is_options_complete") is True
            evidence.append({"revision_hash": _digest(revision), "request_cursor_hash": _digest(cursor),
                "next_cursor_hash": _digest(next_cursor), "total": total, "row_count": len(cleaned),
                "rows_hash": _digest(cleaned)})
            if next_cursor is not None and (next_cursor in seen_cursors or not cleaned):
                raise ValueError("pagination cycle or empty nonterminal page")
            is_complete = next_cursor is None and len(rows) == total and (source == "ownership" or seen_ids == set(requested))
            if observer is not None:
                if len(evidence) == 1 and getattr(port, "is_stable_snapshot_revision", False) is True:
                    reused = observer.reuse_complete(evidence[0], page.get("is_options_complete") is True)
                    if reused is not None:
                        return SourceCollection(**reused)
                observer.record_page(cleaned, evidence[-1], next_cursor is None, page.get("is_options_complete") is True, is_complete)
            if next_cursor is None:
                return SourceCollection("complete" if is_complete else "partial", tuple(rows), tuple(evidence),
                                        is_options_complete and is_complete)
            seen_cursors.add(next_cursor)
            cursor = next_cursor
    except CollectionSourceError as error:
        if error.is_auth_required:
            if observer is not None:
                observer.finish("auth_required")
            return SourceCollection("auth_required", (), tuple(evidence))
    except (TimeoutError, OSError):
        pass
    except (ValueError, KeyError, TypeError):
        if observer is not None:
            observer.finish("failed")
        return SourceCollection("failed", (), tuple(evidence))
    if observer is not None:
        observer.finish("partial" if rows else "failed")
    return SourceCollection("partial" if rows else "failed", tuple(rows), tuple(evidence))


def _clean_rows(source, rows, member, scope, captured_at, reference):
    ownership = rows if source == "ownership" else []
    details = rows if source == "details" else []
    effects = rows if source == "effects" else []
    raw = capture_raw_training(scope, captured_at, reference, ({"area_id": member.area_id, "member_id": member.member_id,
        "ownership": {"status": "partial", "characters": ownership},
        "details": {"status": "partial", "requested_codes": [], "character_details": details, "state_effects": effects}},))
    cleaned = raw.payload["members"][0]
    return cleaned["ownership"]["characters"] if source == "ownership" else cleaned["details"]["character_details" if source == "details" else "state_effects"]


def _digest(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()
