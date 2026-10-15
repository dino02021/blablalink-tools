"""Signed local control-request contracts; transport and hosted deployment are absent."""

from dataclasses import asdict, dataclass, field
import hashlib
import hmac
import json
import math
import re

from application.raid_codec import restore_problem
from application.tasks import StateConflict, TaskSpec, validate_identifier
from domain.snapshots import canonical_json

COMPUTE_COMMANDS = frozenset({"recompute_healthcheck", "generate_raid_draft"})


class RequestDenied(ValueError):
    """Safe public error; do not expose signature, key, body or registry contents."""


@dataclass(frozen=True)
class RequestPrincipal:
    principal_id: str
    commands: frozenset[str]
    scopes: frozenset[str]
    workbooks: frozenset[str]
    keys: dict[str, bytes] = field(repr=False, compare=False)

    def __post_init__(self):
        validate_identifier(self.principal_id)
        if not self.keys or any(not isinstance(key, bytes) or len(key) < 32 for key in self.keys.values()):
            raise ValueError("explicit in-memory signing keys required")
        for key_id in self.keys:
            validate_identifier(key_id)
        for name in ("commands", "scopes", "workbooks"):
            values = frozenset(getattr(self, name))
            for value in values:
                validate_identifier(value)
            object.__setattr__(self, name, values)
        object.__setattr__(self, "keys", dict(self.keys))


def sign_request(body: bytes, principal_id: str, key_id: str, key: bytes,
                 timestamp: int, nonce: str, request_id: str) -> dict:
    fields = {"schema_version": 1, "principal_id": principal_id, "key_id": key_id, "timestamp": timestamp,
        "nonce": nonce, "request_id": request_id, "method": "POST", "path": "/v1/requests",
        "body_sha256": hashlib.sha256(body).hexdigest()}
    return {**fields, "signature": hmac.new(key, canonical_json(fields).encode(), hashlib.sha256).hexdigest()}


def verify_request(envelope: dict, body: bytes, principal: RequestPrincipal, now: float) -> TaskSpec:
    required = {"schema_version", "principal_id", "key_id", "timestamp", "nonce", "request_id", "method", "path", "body_sha256", "signature"}
    if (not isinstance(envelope, dict) or set(envelope) != required or not isinstance(body, bytes)
            or not 1 <= len(body) <= 65536 or type(now) not in (int, float) or not math.isfinite(now)):
        raise RequestDenied("invalid_request")
    if (type(envelope["schema_version"]) is not int or envelope["schema_version"] != 1
            or type(envelope["timestamp"]) is not int or abs(now - envelope["timestamp"]) > 300
            or envelope["principal_id"] != principal.principal_id
            or envelope["method"] != "POST" or envelope["path"] != "/v1/requests"):
        raise RequestDenied("invalid_request")
    for name in ("key_id", "nonce", "request_id"):
        if not isinstance(envelope[name], str) or not 1 <= len(envelope[name]) <= 256 or any(ord(c) < 32 for c in envelope[name]):
            raise RequestDenied("invalid_request")
    key = principal.keys.get(envelope["key_id"])
    if key is None or not isinstance(envelope["signature"], str) or not isinstance(envelope["body_sha256"], str):
        raise RequestDenied("invalid_signature")
    if any(re.fullmatch(r"[0-9a-f]{64}", envelope[name]) is None for name in ("signature", "body_sha256")):
        raise RequestDenied("invalid_signature")
    unsigned = {k: v for k, v in envelope.items() if k != "signature"}
    expected = hmac.new(key, canonical_json(unsigned).encode(), hashlib.sha256).hexdigest()
    if (not hmac.compare_digest(envelope["signature"], expected)
            or not hmac.compare_digest(envelope["body_sha256"], hashlib.sha256(body).hexdigest())):
        raise RequestDenied("invalid_signature")
    try:
        payload = json.loads(body, object_pairs_hook=_unique_object)
        if not isinstance(payload, dict) or set(payload) != {"task"}:
            raise ValueError("invalid task envelope")
        task = TaskSpec(**payload["task"])
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise RequestDenied("invalid_body") from None
    if (task.command not in COMPUTE_COMMANDS or task.command not in principal.commands
            or task.scope_id not in principal.scopes):
        raise RequestDenied("scope_denied")
    return task


def resolve_request_workbooks(store, task: TaskSpec) -> frozenset[str]:
    """Never trust a separately declared list of authorized target resources."""
    if task.command == "recompute_healthcheck":
        versions = task.version_map
        inputs = store.get_document(versions["union_inputs"])
        training = store.get_document(versions["training"])
        config = store.get_document(versions["healthcheck_config"])
        if (inputs.envelope["kind"] != "union_inputs" or training.envelope["kind"] != "training"
                or config.envelope["kind"] != "healthcheck_config"
                or any(doc.envelope["scope_id"] != task.scope_id for doc in (inputs, training))
                or config.envelope["scope_id"] not in ("common", task.scope_id)
                or len({doc.envelope["reference_version"] for doc in (inputs, training, config)}) != 1):
            raise RequestDenied("input_scope_denied")
        resource = inputs.payload["healthcheck_route"]["resource"]
        validate_identifier(resource)
        return frozenset({resource})
    if task.command == "generate_raid_draft":
        problem_id = task.version_map["problem"]
        head = store.input_head(task.scope_id)
        problem = restore_problem(store.get_result(problem_id))
        if not head or head["problem_id"] != problem_id or problem.code_sha != task.code_sha:
            raise RequestDenied("input_scope_denied")
        return frozenset()
    raise RequestDenied("command_denied")


def encode_request_task(task: TaskSpec) -> bytes:
    return canonical_json({"task": asdict(task)}).encode()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result
