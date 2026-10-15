"""Compile fixed, published report artifacts into explicit local send candidates."""

from dataclasses import asdict, dataclass
import hashlib

from application.delivery import DeliverySpec
from application.tasks import StateConflict, validate_identifier
from domain.snapshots import MemberKey, canonical_json

FENCE = chr(96) * 3


@dataclass(frozen=True)
class ReportSendCandidate:
    scope_id: str
    intent_id: str
    job_id: str
    result_id: str
    route_snapshot_id: str
    deliveries: tuple[DeliverySpec, ...]

    def __post_init__(self):
        for value in (self.scope_id, self.intent_id, self.job_id, self.result_id, self.route_snapshot_id):
            validate_identifier(value)
        deliveries = tuple(self.deliveries)
        if not deliveries or any(not isinstance(d, DeliverySpec) or d.intent_id != self.intent_id or d.route_version != self.route_snapshot_id for d in deliveries):
            raise ValueError("nonempty deliveries bound to one intent and route version required")
        if len({d.member_id for d in deliveries}) != len(deliveries):
            raise ValueError("duplicate delivery member")
        object.__setattr__(self, "deliveries", deliveries)

    @property
    def serialized(self) -> str:
        return canonical_json(asdict(self))

    @property
    def candidate_id(self) -> str:
        return hashlib.sha256(self.serialized.encode()).hexdigest()


class ReportCandidateValidator:
    def __init__(self, store):
        self.store = store

    def __call__(self, candidate: ReportSendCandidate):
        job = self.store.get_job(candidate.job_id)
        if (job.spec.scope_id != candidate.scope_id or job.state != "succeeded" or job.result_id != candidate.result_id
                or not self.store.is_job_current(job.job_id)):
            raise StateConflict("unconfirmed send candidate is stale or unpublished")
        if self.store.get_document(candidate.route_snapshot_id).envelope["scope_id"] != candidate.scope_id:
            raise StateConflict("report route snapshot scope differs")
        expected = compile_report_candidate(self.store, candidate.job_id, candidate.route_snapshot_id, candidate.intent_id)
        if expected != candidate:
            raise StateConflict("send candidate differs from sealed report and route")


def compile_report_candidate(store, job_id: str, route_snapshot_id: str, intent_id: str) -> ReportSendCandidate | None:
    validate_identifier(intent_id)
    job = store.get_job(job_id)
    if job.spec.command != "recompute_healthcheck" or job.state != "succeeded" or job.result_id is None:
        raise StateConflict("only a successfully published healthcheck can be sent")
    result = store.get_result(job.result_id)
    books = result.get("workbooks", {})
    if len(books) != 1:
        raise ValueError("one sealed report workbook required")
    generated = next(iter(books.values()))
    versions = job.spec.version_map
    if (generated["config_id"] != versions["healthcheck_config"] or generated["snapshot_id"] != versions["training"]
            or generated["code_sha"] != job.spec.code_sha):
        raise StateConflict("report artifact differs from job versions")
    routes = store.get_document(route_snapshot_id)
    training = store.get_document(versions["training"])
    if (routes.envelope["kind"] != "union_inputs" or routes.envelope["scope_id"] != job.spec.scope_id
            or routes.envelope["reference_version"] != training.envelope["reference_version"]
            or routes.payload.get("routing_kind") != "report_routes"):
        raise StateConflict("report routing source differs")
    indexed = {}
    for row in routes.payload["routes"]:
        member = MemberKey(**row["member"])
        if member.key in indexed:
            raise ValueError("duplicate member route")
        validate_identifier(row["target_thread"])
        indexed[member.key] = row
    deliveries = []
    for report in generated["reports"]:
        if report.get("is_complete") is not True or report.get("warnings") != []:
            raise StateConflict("incomplete report cannot enter outbox")
        if not report["text"]:
            continue
        member = MemberKey(**report["member"])
        route = indexed.get(member.key)
        if route is None:
            raise ValueError("report member lacks a fixed route")
        user_id = route.get("mention_user_id")
        if user_id is not None and (not isinstance(user_id, str) or not user_id.isascii() or not user_id.isdigit() or len(user_id) > 32):
            raise ValueError("invalid explicit mention user identity")
        parts = format_report_parts(report["text"], user_id)
        deliveries.append(DeliverySpec(intent_id, generated["calculation_id"], member.key, route_snapshot_id,
            route["target_thread"], parts, (user_id,) if user_id else ()))
    if not deliveries:
        return None
    candidate = ReportSendCandidate(job.spec.scope_id, intent_id, job_id, job.result_id, route_snapshot_id, tuple(deliveries))
    if not store.is_job_current(job_id):
        raise StateConflict("unconfirmed send candidate is stale")
    return candidate


def restore_report_candidate(data: dict) -> ReportSendCandidate:
    return ReportSendCandidate(**{**data, "deliveries": tuple(DeliverySpec(**row) for row in data["deliveries"])})


def format_report_parts(text: str, user_id: str | None = None) -> tuple[str, ...]:
    marker = "\n" + FENCE + "cs\n"
    if not isinstance(text, str) or text.count(FENCE) != 2 or marker not in text or not text.endswith("\n" + FENCE):
        raise ValueError("expected complete generated code-block report")
    header, body = text.split(marker, 1)
    body = body[:-(len(FENCE) + 1)]
    if user_id:
        if not isinstance(user_id, str) or not user_id.isascii() or not user_id.isdigit() or len(user_id) > 32:
            raise ValueError("invalid explicit mention")
        header = f"<@{user_id}> " + header.removeprefix("@")
    if _units(header) > 512 or _units(body) > 200000 or not body:
        raise ValueError("report exceeds local formatting bounds or is empty")
    parts = []
    while body:
        prefix = (header + "\n" if not parts else "") + FENCE + "cs\n"
        suffix = "\n" + FENCE
        capacity = 2000 - _units(prefix) - _units(suffix)
        units, cut = 0, 0
        for character in body:
            size = 2 if ord(character) > 0xffff else 1
            if units + size > capacity:
                break
            units += size
            cut += 1
        if cut < len(body):
            newline = body.rfind("\n", 0, cut)
            if newline >= 0:
                cut = newline + 1
        if cut == 0:
            raise ValueError("report header leaves no body capacity")
        parts.append(prefix + body[:cut] + suffix)
        body = body[cut:]
    return tuple(parts)


def _units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2
