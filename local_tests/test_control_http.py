from types import SimpleNamespace

import pytest

from application.control_http import handle_control_request
from application.control_requests import RequestDenied
from application.tasks import StateConflict


def test_disabled_handler_never_touches_gate():
    gate = RecordingGate()
    result = handle_control_request("POST", "/v1/requests", {}, b"{}", gate)
    assert result.status == 503 and gate.calls == 0


@pytest.mark.parametrize("change,status", [("method", 405), ("path", 404), ("body", 413), ("duplicate_header", 400), ("content_type", 400), ("envelope", 400)])
def test_transport_rejections_do_not_submit(change, status):
    gate = RecordingGate()
    method, path, body = "POST", "/v1/requests", b"{}"
    headers = {"Content-Type": "application/json", "X-Control-Envelope": "{}"}
    if change == "method":
        method = "GET"
    elif change == "path":
        path += "?unexpected=true"
    elif change == "body":
        body = b"x" * 65537
    elif change == "duplicate_header":
        headers["content-type"] = "application/json"
    elif change == "content_type":
        headers["Content-Type"] = "text/plain"
    else:
        headers["X-Control-Envelope"] = '{"a":1,"a":2}'
    response = handle_control_request(method, path, headers, body, gate, True)
    assert response.status == status and gate.calls == 0


@pytest.mark.parametrize("error,status", [(RequestDenied("sensitive fixture"), 403), (StateConflict("sensitive fixture"), 409), (OSError("sensitive fixture"), 503)])
def test_internal_failure_details_never_escape(error, status):
    gate = RecordingGate(error)
    response = handle_control_request("POST", "/v1/requests", {"content-type": "application/json", "x-control-envelope": "{}"}, b"{}", gate, True)
    assert response.status == status
    assert "sensitive" not in str(response.body)


def test_success_is_accepted_only_after_gate_returns_durable_job():
    gate = RecordingGate()
    response = handle_control_request("POST", "/v1/requests", {"content-type": "application/json", "x-control-envelope": "{}"}, b"{}", gate, True)
    assert response.status == 202 and response.body == {"state": "accepted", "job_id": "job_mock", "job_state": "queued"}
    assert dict(response.headers)["Cache-Control"] == "no-store"


class RecordingGate:
    def __init__(self, error=None):
        self.error, self.calls = error, 0

    def submit_signed(self, envelope, body):
        self.calls += 1
        if self.error:
            raise self.error
        return SimpleNamespace(job_id="job_mock", state="queued")
