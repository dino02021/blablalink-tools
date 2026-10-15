"""Transport-neutral local HTTP contract. No server or external provider is started."""

from dataclasses import dataclass
import json
import sqlite3

from application.control_requests import RequestCapacity, RequestDenied
from application.tasks import StateConflict

RESPONSE_HEADERS = (("Content-Type", "application/json; charset=utf-8"), ("Cache-Control", "no-store"))

@dataclass(frozen=True)
class ControlResponse:
    status: int
    body: dict
    headers: tuple[tuple[str, str], ...] = RESPONSE_HEADERS


def handle_control_request(method, path, headers, body, gate, is_enabled=False) -> ControlResponse:
    if is_enabled is not True:
        return ControlResponse(503, {"error": "control_disabled"})
    if path != "/v1/requests":
        return ControlResponse(404, {"error": "route_not_found"})
    if method != "POST":
        return ControlResponse(405, {"error": "method_not_allowed"})
    if not isinstance(body, bytes) or not 1 <= len(body) <= 65536:
        return ControlResponse(413, {"error": "body_size_invalid"})
    if not isinstance(headers, dict) or len(headers) > 64:
        return ControlResponse(400, {"error": "headers_invalid"})
    lowered = {}
    for key, value in headers.items():
        if (not isinstance(key, str) or not isinstance(value, str) or len(key) > 128 or len(value) > 8192
                or key.lower() in lowered):
            return ControlResponse(400, {"error": "headers_invalid"})
        lowered[key.lower()] = value
    content_type = lowered.get("content-type", "").split(";", 1)[0].strip().lower()
    envelope_text = lowered.get("x-control-envelope", "")
    try:
        envelope_size = len(envelope_text.encode("utf-8"))
    except UnicodeError:
        return ControlResponse(400, {"error": "headers_invalid"})
    if content_type != "application/json" or not 1 <= envelope_size <= 4096:
        return ControlResponse(400, {"error": "headers_invalid"})
    try:
        envelope = json.loads(envelope_text, object_pairs_hook=_unique_object)
        job = gate.submit_signed(envelope, body)
    except RequestDenied:
        return ControlResponse(403, {"error": "request_denied"})
    except RequestCapacity as error:
        return ControlResponse(429, {"error": "control_capacity_blocked"}, RESPONSE_HEADERS + (("Retry-After", str(error.retry_after)),))
    except StateConflict:
        return ControlResponse(409, {"error": "request_conflict"})
    except (ValueError, KeyError, TypeError, RecursionError):
        return ControlResponse(400, {"error": "request_invalid"})
    except (sqlite3.Error, OSError):
        return ControlResponse(503, {"error": "control_unavailable"})
    return ControlResponse(202, {"state": "accepted", "job_id": job.job_id, "job_state": job.state})


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate control envelope key")
        result[key] = value
    return result
