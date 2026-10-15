import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from adapters.local_request_gate import LocalRequestGate
from adapters.mock_sheets import MockSheets
from application.control_requests import RequestPrincipal
from domain.snapshots import capture_document
from executors.local.demo import run_demo
from executors.local.worker import run_worker

KEY = "mock-only-signing-key-never-production-0001"


def test_gs_signed_hint_reaches_python_and_only_prepares_candidate(tmp_path):
    directory = tmp_path / "mock"
    summary = run_demo(directory)
    policy = RequestPrincipal("principal_mock", frozenset({"reconcile"}), frozenset({"common"}), frozenset({"standard_demo"}), {"key_mock": KEY.encode()})
    gate = LocalRequestGate(directory / "state.sqlite", (policy,), clock=fixed_now)
    try:
        route = capture_document("union_inputs", "common", "2026-10-15T00:00:00Z", "reference_demo",
            {"reconcile_kind": "structured_healthcheck_standard", "resource": "standard_demo", "role_ids": ["role_demo"], "function_ids": []})
        gate.put_document(route)
        config = bridge_config(route.snapshot_id)
        result = run_bridge({"config": config, "key": KEY}, tmp_path)
        assert result["outcome"]["state"] == "accepted"
        request, = result["requests"]
        envelope = json.loads(request["options"]["headers"]["X-Control-Envelope"])
        job = gate.submit_signed(envelope, request["options"]["payload"].encode())
        assert gate.submit_signed(envelope, request["options"]["payload"].encode()).job_id == job.job_id
        before = MockSheets(directory / "workbooks").read("output_demo")
        processed = run_worker(directory, "mock-release-v1")
        assert processed["processed"][0]["state"] == "succeeded"
        sealed = gate.get_result(gate.get_job(job.job_id).result_id)
        assert sealed["candidate_id"] == summary["pending_candidate_id"]
        assert gate.config_head("healthcheck", "common") == summary["published_config_id"]
        assert MockSheets(directory / "workbooks").read("output_demo") == before
        assert request["options"]["followRedirects"] is False
    finally:
        gate.close()


@pytest.mark.parametrize("scenario,expected", [("disabled", "disabled"), ("other_book", "ignored"), ("other_tab", "ignored"), ("outside", "ignored")])
def test_ignored_edits_never_read_key_or_send(scenario, expected, tmp_path):
    data = {"config": bridge_config("route"), "key": KEY}
    if scenario == "disabled":
        data["config"]["enabled"] = False
    if scenario == "other_book":
        data["spreadsheet_id"] = "other"
    if scenario == "other_tab":
        data["sheet_id"] = 2
    if scenario == "outside":
        data["row"] = 999
    result = run_bridge(data, tmp_path)
    assert result["outcome"]["state"] == expected
    assert result["requests"] == []
    assert "BBL_CONTROL_SIGNING_KEY" not in result["reads"]


@pytest.mark.parametrize("response,expected", [("timeout", "acceptance_unknown"), ("rejected", "not_accepted"), ("bad_receipt", "acceptance_unknown")])
def test_thin_bridge_does_not_confuse_transport_with_completed_computation(response, expected, tmp_path):
    result = run_bridge({"config": bridge_config("route"), "key": KEY, "response": response}, tmp_path)
    assert result["outcome"]["state"] == expected
    assert len(result["requests"]) == 1  # no blind retry


def test_timer_uses_same_signed_hint_contract(tmp_path):
    result = run_bridge({"config": bridge_config("route"), "key": KEY, "is_timer": True}, tmp_path)
    assert result["outcome"]["state"] == "accepted"
    task = json.loads(result["requests"][0]["options"]["payload"])["task"]
    assert task["command"] == "reconcile" and task["family"] == "config_reconcile"


def bridge_config(route):
    return {"enabled": True, "spreadsheet_id": "book_mock", "sheet_id": 1,
        "input_range": {"start_row": 2, "end_row": 20, "start_column": 2, "end_column": 15},
        "control_url": "https://mock.invalid/v1/requests", "principal_id": "principal_mock", "key_id": "key_mock",
        "scope_id": "common", "code_sha": "mock-release-v1", "route_snapshot_id": route}


def run_bridge(data, directory):
    node = shutil.which("node")
    assert node, "local thin GS contract tests require Node"
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP") if key in os.environ}
    result = subprocess.run([node, str(Path(__file__).with_name("gs_bridge_runner.js"))], input=json.dumps(data),
        capture_output=True, text=True, encoding="utf-8", check=True, timeout=5, cwd=directory, env=env)
    return json.loads(result.stdout)


def fixed_now():
    return 1000
