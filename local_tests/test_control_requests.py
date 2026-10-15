from dataclasses import replace

import pytest

from adapters.local_request_gate import LocalRequestGate
from adapters.local_state import LocalStateStore
from application.control_requests import RequestDenied, RequestPrincipal, encode_request_task, sign_request
from application.tasks import TaskSpec
from domain.healthcheck import ELEMENTS
from domain.raid import RaidMember
from domain.raid_planner import RaidProblem
from domain.snapshots import MemberKey, capture_document
from executors.local.demo import run_demo

KEY = b"mock-only-signing-key-never-production-0001"


@pytest.fixture
def control(tmp_path):
    directory = tmp_path / "mock"
    summary = run_demo(directory)
    policy = RequestPrincipal("principal_mock", frozenset({"recompute_healthcheck"}), frozenset({"union_demo"}),
        frozenset({"output_demo"}), {"key_mock": KEY})
    gate = LocalRequestGate(directory / "state.sqlite", (policy,), clock=fixed_now)
    spec = gate.get_job(summary["job_id"]).spec
    body = encode_request_task(spec)
    envelope = sign(body)
    yield gate, spec, body, envelope, directory, policy
    gate.close()


def test_exact_retry_new_nonce_and_restart_reuse_job(control):
    gate, _, body, envelope, directory, policy = control
    first = gate.submit_signed(envelope, body)
    assert gate.submit_signed(envelope, body).job_id == first.job_id
    assert gate.submit_signed(sign(body, nonce="fresh_nonce"), body).job_id == first.job_id
    assert gate._connection.execute("SELECT actor_status FROM control_requests").fetchone()[0] == "unknown"
    reopened = LocalRequestGate(directory / "state.sqlite", (policy,), clock=fixed_now)
    try:
        assert reopened.submit_signed(envelope, body).job_id == first.job_id
    finally:
        reopened.close()


@pytest.mark.parametrize("field,value", [("timestamp", 699), ("timestamp", 1301), ("timestamp", True),
    ("method", "GET"), ("path", "/v1/requests?x=1"), ("schema_version", True),
    ("signature", "界" * 64), ("body_sha256", "g" * 64), ("key_id", "other"), ("principal_id", [])])
def test_bad_signed_metadata_is_denied(control, field, value):
    gate, _, body, envelope, _, _ = control
    envelope = {**envelope, field: value}
    with pytest.raises(RequestDenied):
        gate.submit_signed(envelope, body)
    assert gate._connection.execute("SELECT COUNT(*) FROM control_requests").fetchone()[0] == 0


def test_modified_body_and_nonce_reuse_are_denied(control):
    gate, _, body, envelope, _, _ = control
    with pytest.raises(RequestDenied):
        gate.submit_signed(envelope, body + b" ")
    gate.submit_signed(envelope, body)
    with pytest.raises(RequestDenied, match="nonce_reused"):
        gate.submit_signed(sign(body, request_id="other_request"), body)


def test_signed_client_cannot_escape_desired_target_by_changing_family(control):
    gate, spec, _, _, _, _ = control
    body = encode_request_task(replace(spec, family="invented_family"))
    with pytest.raises(RequestDenied, match="task_family_denied"):
        gate.submit_signed(sign(body), body)
    assert gate._connection.execute("SELECT COUNT(*) FROM control_requests").fetchone()[0] == 0


@pytest.mark.parametrize("change", ["valid", "code", "scope_head"])
def test_raid_request_uses_scoped_problem_head_and_code(control, change):
    gate, _, _, _, _, policy = control
    gate.principals[policy.principal_id] = replace(policy, commands=frozenset({"generate_raid_draft"}))
    member = RaidMember(MemberKey("area", "member"), "mock", 100)
    problem = RaidProblem((member,), (("0",) * 5,) * 3, ELEMENTS, 100, ("10",) * 5, (), (), (),
        (("inputs", "mock"),), "code")
    gate.observe_problem("union_demo", problem, 0)
    if change == "scope_head":
        gate.observe_problem("union_demo", replace(problem, input_versions=(("inputs", "new"),)), 1)
    task = TaskSpec("generate_raid_draft", "union_demo", "other" if change == "code" else "code", {"problem": problem.problem_id}, family="raid")
    body = encode_request_task(task)
    if change == "valid":
        assert gate.submit_signed(sign(body), body).spec == task
    else:
        with pytest.raises(RequestDenied):
            gate.submit_signed(sign(body), body)


def test_request_id_cannot_change_payload_even_with_valid_signature(control):
    gate, spec, body, envelope, _, _ = control
    gate.submit_signed(envelope, body)
    changed = encode_request_task(replace(spec, priority=5))
    with pytest.raises(RequestDenied, match="request_changed"):
        gate.submit_signed(sign(changed, nonce="new_nonce"), changed)


def test_actual_route_resource_is_authorized_from_fixed_inputs(control):
    gate, spec, _, _, _, _ = control
    inputs = gate.get_document(spec.version_map["union_inputs"])
    payload = inputs.payload
    payload["healthcheck_route"]["resource"] = "not_authorized_workbook"
    changed = capture_document("union_inputs", "union_demo", "2026-10-15T00:01:00Z", "reference_demo", payload)
    gate.put_document(changed)
    body = encode_request_task(replace(spec, versions={**spec.version_map, "union_inputs": changed.snapshot_id}))
    with pytest.raises(RequestDenied, match="workbook_denied"):
        gate.submit_signed(sign(body), body)


@pytest.mark.parametrize("change", ["scope", "command", "key_revoked"])
def test_valid_signature_does_not_bypass_current_policy(control, change):
    gate, spec, body, envelope, _, policy = control
    if change == "scope":
        body = encode_request_task(replace(spec, scope_id="other_scope"))
    elif change == "command":
        body = encode_request_task(replace(spec, command="publish_config"))
    else:
        gate.principals[policy.principal_id] = replace(policy, keys={"replacement": b"x" * 32})
    with pytest.raises(RequestDenied):
        gate.submit_signed(sign(body), body)


@pytest.mark.parametrize("body", [b'{"task":{},"task":{}}', b'{"task":{},"workbooks":["output_demo"]}', b"x" * 65537],
    ids=["duplicate_keys", "extra_resource_claim", "oversized"])
def test_duplicate_keys_extra_claims_and_oversized_body_are_denied(control, body):
    gate, _, _, _, _, _ = control
    with pytest.raises(RequestDenied):
        gate.submit_signed(sign(body), body)


def test_crash_between_replay_record_and_job_submission_is_recoverable(control, monkeypatch):
    gate, _, body, envelope, _, _ = control
    monkeypatch.setattr(gate, "submit_job", interrupted_submission)
    with pytest.raises(OSError):
        gate.submit_signed(envelope, body)
    assert gate._connection.execute("SELECT COUNT(*) FROM control_requests").fetchone()[0] == 1
    monkeypatch.setattr(gate, "submit_job", LocalStateStore.submit_job.__get__(gate))
    assert gate.submit_signed(envelope, body).job_id


def interrupted_submission(*args, **kwargs):
    raise OSError("synthetic interruption before coordinator commit")


def sign(body, nonce="nonce_mock", request_id="request_mock"):
    return sign_request(body, "principal_mock", "key_mock", KEY, 1000, nonce, request_id)


def fixed_now():
    return 1000
