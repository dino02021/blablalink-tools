"""Explicit reference compatibility evidence; never relabels the source raw snapshot."""

from dataclasses import asdict, dataclass
import hashlib

from application.reference_catalog import read_reference
from domain.normalization import normalize_training
from domain.snapshots import Document, TrainingRecord, canonical_json, capture_document, capture_training, read_training


@dataclass(frozen=True)
class ReferenceProjectionPlan:
    raw_snapshot_id: str
    source_reference_id: str
    target_reference_id: str
    code_sha: str
    training_id: str | None
    blockers: tuple[str, ...]
    schema_version: int = 1

    @property
    def plan_id(self) -> str:
        return hashlib.sha256(canonical_json(asdict(self)).encode()).hexdigest()


@dataclass(frozen=True)
class ReferenceProjection:
    plan: ReferenceProjectionPlan
    training: Document | None


def project_reference_training(raw, source_reference, target_reference, code_sha) -> ReferenceProjection:
    source, source_prefixes = read_reference(source_reference)
    target, target_prefixes = read_reference(target_reference)
    if (not isinstance(code_sha, str) or not code_sha.strip() or raw.envelope["kind"] != "raw"
            or raw.envelope["reference_version"] != source.reference.version
            or source_reference.envelope["scope_id"] != target_reference.envelope["scope_id"]
            or source_reference.envelope["scope_id"] not in ("common", raw.envelope["scope_id"])):
        raise ValueError("reference projection source dependencies differ")
    dependencies = dict(raw_snapshot_id=raw.snapshot_id, source_reference_id=source_reference.snapshot_id,
        target_reference_id=target_reference.snapshot_id, code_sha=code_sha)
    blockers = []
    if raw.payload.get("reference_role_ids") != list(source.reference.role_ids):
        blockers.append("role_coverage_unproven")
    if set(target.reference.role_ids) - set(source.reference.role_ids):
        blockers.append("ownership_refresh_required")
    if any(source_prefixes.get(prefix) != function for prefix, function in target_prefixes.items()):
        blockers.append("function_mapping_refresh_required")
    if len(raw.payload.get("members", [])) > 300:
        raise ValueError("reference projection roster exceeds bound")
    if blockers:
        return ReferenceProjection(ReferenceProjectionPlan(**dependencies, training_id=None, blockers=tuple(blockers)), None)
    normalized = normalize_training(raw, source.reference.role_ids, source_prefixes)
    if normalized.is_auth_required:
        return ReferenceProjection(ReferenceProjectionPlan(**dependencies, training_id=None, blockers=("auth_required",)), None)
    roles, functions = set(target.reference.role_ids), set(target.reference.function_ids)
    records = tuple(TrainingRecord(record.member, record.name_code, record.ownership_status, record.metrics,
        tuple((function, value) for function, value in record.traits if function in functions))
        for record in read_training(normalized.training) if record.name_code in roles)
    base = capture_training(raw.envelope["scope_id"], raw.envelope["captured_at"], target.reference.version, records)
    training = capture_document("training", raw.envelope["scope_id"], raw.envelope["captured_at"], target.reference.version,
        {**base.payload, "raw_snapshot_id": raw.snapshot_id, "normalizer_version": 1,
            "reference_projection": {"schema_version": 1, "source_reference_id": source_reference.snapshot_id,
                "target_reference_id": target_reference.snapshot_id, "source_reference_version": source.reference.version,
                "source_training_id": normalized.training.snapshot_id, "code_sha": code_sha}})
    return ReferenceProjection(ReferenceProjectionPlan(**dependencies, training_id=training.snapshot_id, blockers=()), training)


def verify_reference_projection(projection, raw, source_reference, target_reference, code_sha) -> None:
    if projection != project_reference_training(raw, source_reference, target_reference, code_sha):
        raise ValueError("reference projection receipt differs from its inputs")
