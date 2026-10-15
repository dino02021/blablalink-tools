"""Content-addressed, immutable JSON snapshots; no filesystem or credentials."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Mapping

from .healthcheck import MetricRule, RoleRule, parse_number


Scalar = str | int | float | None
SECRET_KEYS = frozenset({
    "password", "cookie", "cookies", "authorization", "access_token",
    "refresh_token", "session", "sessions", "storage_state", "credentials",
    "service_account", "private_key", "client_secret", "token", "secret",
    "api_key", "session_token", "bearer_token",
})


@dataclass(frozen=True, order=True)
class MemberKey:
    area_id: str
    member_id: str

    def __post_init__(self) -> None:
        _validate_id(self.area_id)
        _validate_id(self.member_id)

    @property
    def key(self) -> str:
        return canonical_json([self.area_id, self.member_id])


@dataclass(frozen=True)
class Document:
    """The only retained payload is a canonical JSON string, never a caller dict."""
    serialized: str

    def __post_init__(self) -> None:
        envelope = json.loads(self.serialized, object_pairs_hook=_unique_object)
        if not isinstance(envelope, dict) or set(envelope) != {
            "schema_version", "kind", "scope_id", "captured_at", "reference_version", "payload"
        }:
            raise ValueError("invalid snapshot envelope")
        if type(envelope["schema_version"]) is not int or envelope["schema_version"] != 1:
            raise ValueError("unsupported snapshot schema")
        if envelope["kind"] not in ("raw", "training", "healthcheck_config", "union_inputs"):
            raise ValueError("unsupported snapshot kind")
        _validate_id(envelope["scope_id"])
        _validate_id(envelope["reference_version"])
        envelope["captured_at"] = _canonical_time(envelope["captured_at"])
        if not isinstance(envelope["payload"], dict):
            raise ValueError("snapshot payload must be an object")
        object.__setattr__(self, "serialized", canonical_json(envelope))

    @property
    def snapshot_id(self) -> str:
        return hashlib.sha256(self.serialized.encode("utf-8")).hexdigest()

    @property
    def payload_hash(self) -> str:
        return hashlib.sha256(canonical_json(self.payload).encode("utf-8")).hexdigest()

    @property
    def payload(self) -> dict:
        return json.loads(self.serialized)["payload"]

    @property
    def envelope(self) -> dict:
        return json.loads(self.serialized)


@dataclass(frozen=True)
class TrainingRecord:
    member: MemberKey
    name_code: str
    ownership_status: str
    metrics: tuple[tuple[str, Scalar], ...] = ()
    traits: tuple[tuple[str, Scalar], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.member, MemberKey):
            raise ValueError("invalid member key")
        _validate_id(self.name_code)
        if self.ownership_status not in ("owned", "not_owned", "unknown"):
            raise ValueError("invalid ownership status")
        object.__setattr__(self, "metrics", _freeze_pairs(self.metrics))
        object.__setattr__(self, "traits", _freeze_pairs(self.traits))
        if self.ownership_status == "not_owned" and (self.metrics or self.traits):
            raise ValueError("not_owned record cannot contain training values")

    @property
    def key(self) -> tuple[str, str]:
        return self.member.key, self.name_code


@dataclass(frozen=True)
class RoleLabel:
    name_code: str
    display_name: str
    priority: str = ""
    trait_labels: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        _validate_id(self.name_code)
        if not isinstance(self.display_name, str) or not isinstance(self.priority, str):
            raise ValueError("display labels must be strings")
        labels = _freeze_pairs(self.trait_labels)
        if any(not isinstance(value, str) for _, value in labels):
            raise ValueError("trait labels must be strings")
        object.__setattr__(self, "trait_labels", labels)


@dataclass(frozen=True)
class Override:
    member: MemberKey
    name_code: str
    namespace: str
    field_id: str
    value: Scalar = None
    operation: str = "set"
    is_enabled: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.member, MemberKey):
            raise ValueError("invalid member")
        _validate_id(self.name_code)
        _validate_id(self.field_id)
        if self.namespace not in ("metric", "function") or self.operation not in ("set", "clear"):
            raise ValueError("invalid override")
        if not isinstance(self.is_enabled, bool):
            raise ValueError("is_enabled must be a boolean")
        _freeze_pairs(((self.field_id, self.value),))

    @property
    def key(self) -> tuple[str, str, str, str]:
        return self.member.key, self.name_code, self.namespace, self.field_id


@dataclass(frozen=True)
class Change:
    member: MemberKey
    name_code: str
    namespace: str
    field_id: str
    before: Scalar
    after: Scalar


def canonical_json(value: object) -> str:
    _validate_json(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def capture_document(kind: str, scope_id: str, captured_at: str, reference_version: str, payload: dict) -> Document:
    return Document(canonical_json({
        "schema_version": 1, "kind": kind, "scope_id": scope_id, "captured_at": captured_at,
        "reference_version": reference_version, "payload": payload,
    }))


def restore_document(serialized: str, expected_id: str) -> Document:
    document = Document(serialized)
    if document.snapshot_id != expected_id:
        raise ValueError("snapshot hash mismatch")
    return document


def capture_training(scope_id: str, captured_at: str, reference_version: str, records: tuple[TrainingRecord, ...]) -> Document:
    _index_records(records)
    rows = []
    for record in sorted(records, key=lambda item: item.key):
        rows.append({
            "area_id": record.member.area_id, "member_id": record.member.member_id,
            "name_code": record.name_code, "ownership_status": record.ownership_status,
            "metrics": dict(record.metrics), "traits_by_function_id": dict(record.traits),
        })
    return capture_document("training", scope_id, captured_at, reference_version, {"records": rows})


def read_training(document: Document) -> tuple[TrainingRecord, ...]:
    if document.envelope["kind"] != "training":
        raise ValueError("expected training snapshot")
    records = tuple(TrainingRecord(
        MemberKey(row["area_id"], row["member_id"]), row["name_code"], row["ownership_status"],
        row["metrics"], row["traits_by_function_id"],
    ) for row in document.payload["records"])
    _index_records(records)
    return records


def capture_config(scope_id: str, captured_at: str, reference_version: str, rules: tuple[RoleRule, ...],
                   labels: tuple[RoleLabel, ...] = ()) -> Document:
    _validate_config(rules, labels)
    return capture_document("healthcheck_config", scope_id, captured_at, reference_version, {
        "rules": [asdict(rule) for rule in rules], "labels": [asdict(label) for label in labels],
    })


def read_config(document: Document) -> tuple[tuple[RoleRule, ...], tuple[RoleLabel, ...]]:
    if document.envelope["kind"] != "healthcheck_config":
        raise ValueError("expected healthcheck configuration")
    payload = document.payload
    rules = tuple(RoleRule(
        row["name_code"], row["elements"], row["role_type"],
        tuple(MetricRule(**metric) for metric in row["metrics"]), row["total_weight"], row["is_score_tracked"],
    ) for row in payload["rules"])
    labels = tuple(RoleLabel(**row) for row in payload["labels"])
    _validate_config(rules, labels)
    return rules, labels


def apply_overrides(records: tuple[TrainingRecord, ...], overrides: tuple[Override, ...]) -> tuple[TrainingRecord, ...]:
    indexed = _index_records(records)
    seen: set[tuple[str, str, str, str]] = set()
    for override in overrides:
        if override.key in seen:
            raise ValueError("duplicate override target")
        seen.add(override.key)
        record = indexed.get((override.member.key, override.name_code))
        if record is None:
            raise ValueError("override target outside snapshot coverage")
        if not override.is_enabled or override.operation == "clear":
            continue
        if record.ownership_status != "owned":
            raise ValueError("metric override cannot establish ownership")
        metrics, traits = dict(record.metrics), dict(record.traits)
        target = metrics if override.namespace == "metric" else traits
        target[override.field_id] = override.value
        indexed[record.key] = TrainingRecord(record.member, record.name_code, "owned", metrics, traits)
    return tuple(indexed[record.key] for record in records)


def diff_training(before: Document, after: Document) -> tuple[Change, ...]:
    if before.envelope["scope_id"] != after.envelope["scope_id"]:
        raise ValueError("cannot compare different unions")
    if before.envelope["reference_version"] != after.envelope["reference_version"]:
        raise ValueError("reference version mismatch")
    old, new = _index_records(read_training(before)), _index_records(read_training(after))
    changes: list[Change] = []
    for key in sorted(old.keys() & new.keys()):
        first, second = old[key], new[key]
        if "unknown" in (first.ownership_status, second.ownership_status):
            continue
        if first.ownership_status != second.ownership_status:
            changes.append(Change(second.member, second.name_code, "ownership", "ownership_status",
                                  first.ownership_status, second.ownership_status))
        if first.ownership_status != "owned" or second.ownership_status != "owned":
            continue
        for namespace, left, right in (("metric", dict(first.metrics), dict(second.metrics)),
                                      ("function", dict(first.traits), dict(second.traits))):
            for field_id in sorted(left.keys() & right.keys()):
                first_number, second_number = parse_number(left[field_id]), parse_number(right[field_id])
                if first_number is not None and second_number is not None and first_number != second_number:
                    changes.append(Change(second.member, second.name_code, namespace, field_id, left[field_id], right[field_id]))
    return tuple(changes)


def metric_values(rule: RoleRule, record: TrainingRecord) -> dict[str, Scalar]:
    metrics, traits = dict(record.metrics), dict(record.traits)
    return {metric.metric_id: traits.get(metric.function_id) if metric.function_id is not None
            else metrics.get(metric.metric_id) for metric in rule.metrics}


def _validate_id(value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("identifiers must be nonempty strings")


def _canonical_time(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must have a timezone")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _freeze_pairs(values: object) -> tuple[tuple[str, Scalar], ...]:
    pairs = tuple(values.items()) if isinstance(values, Mapping) else tuple(tuple(item) for item in values)
    seen = set()
    for key, value in pairs:
        _validate_id(key)
        if key in seen:
            raise ValueError("duplicate field")
        seen.add(key)
        if isinstance(value, bool) or not isinstance(value, (str, int, float, type(None))):
            raise ValueError("training values must be scalar")
        if isinstance(value, (int, float)):
            try:
                is_finite = math.isfinite(value)
            except OverflowError:
                is_finite = False
            if not is_finite:
                raise ValueError("training values must be finite")
    return tuple(sorted(pairs))


def _validate_json(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings")
            normalized = re.sub(r"(?<!^)(?=[A-Z][a-z])", "_", key.strip()).casefold().replace("-", "_")
            if normalized in SECRET_KEYS:
                raise ValueError("credentials must never be captured in snapshots")
            _validate_json(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _validate_json(item)
        return
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    raise ValueError("unsupported JSON value")


def _index_records(records: tuple[TrainingRecord, ...]) -> dict[tuple[str, str], TrainingRecord]:
    indexed = {}
    for record in records:
        if not isinstance(record, TrainingRecord) or record.key in indexed:
            raise ValueError("invalid or duplicate training record")
        indexed[record.key] = record
    return indexed


def _validate_config(rules: tuple[RoleRule, ...], labels: tuple[RoleLabel, ...]) -> None:
    if any(not isinstance(rule, RoleRule) for rule in rules):
        raise ValueError("invalid role rule")
    codes = {rule.name_code for rule in rules}
    if len(codes) != len(rules):
        raise ValueError("duplicate role")
    if any(not isinstance(label, RoleLabel) or label.name_code not in codes for label in labels):
        raise ValueError("unknown label target")
    if len({label.name_code for label in labels}) != len(labels):
        raise ValueError("duplicate label target")


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result
