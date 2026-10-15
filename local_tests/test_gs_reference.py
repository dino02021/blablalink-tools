"""Compare frozen, exact GS helpers inside a local VM; no workbook connection."""

import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from domain.healthcheck import (
    MetricRule, _round_js, competition_ranks, evaluate_metric, parse_number, parse_score_number,
)


ROOT = Path(__file__).resolve().parent


def test_reference_provenance_and_canonical_numeric_parity(tmp_path):
    fixture = ROOT / "fixtures" / "gs_numeric_reference.js"
    metadata = json.loads((ROOT / "fixtures" / "gs_reference_provenance.json").read_text(encoding="utf-8"))
    assert hashlib.sha256(fixture.read_text(encoding="utf-8").encode()).hexdigest() == metadata["fixture_sha256"]
    grades = ["T9", *[f"R{value}" for value in range(16)], *[f"SR{value}" for value in range(16)]]
    values = [None, "", "  ", 0, -1, .5, " 10 ", "1 +", "1 0", "R 0", "broken",
              *grades, *[grade + "+" for grade in grades], *[grade + "-" for grade in grades]]
    actual = _run_reference({"numbers": values}, tmp_path)
    assert actual["isolated"]
    assert actual["numbers"] == [parse_score_number(value) for value in values]


def test_comparison_rank_and_float_round_boundaries_match_gs(tmp_path):
    comparisons = [[value, threshold] for value in ("T9", "R0", "R15", "SR0", "SR15", 0, 5, 10, " 1 0 ")
                   for threshold in ("R0", "R0-", "SR0+", "SR15-", "5", "5+", "5-", " 1 0 ")]
    rounds = [.49999999999999994, .5, 1.5, 2.5, -1.5, -1.5000000000000002, 9007199254740991.0]
    ranks = [[3, 100], [2, 100], [9, 90], [1, 0]]
    actual = _run_reference({"comparisons": comparisons, "rounds": rounds, "ranks": ranks}, tmp_path)
    assert actual["comparisons"] == [evaluate_metric(MetricRule("demo", threshold), value).status == "passed"
                                      for value, threshold in comparisons]
    assert actual["rounds"] == [_round_js(value) for value in rounds]
    assert dict(actual["ranks"]) == {int(key): value for key, value in competition_ranks({str(key): value for key, value in ranks}).items()}
    assert _round_js(math.nextafter(.5, 0)) == 0


def test_intentionally_stricter_inputs_and_distinct_whitespace_rules():
    assert parse_number("1 0") == 10
    assert parse_score_number("1 0") is None
    assert parse_score_number("1 +") == 1
    for value in ("0x10", "0b10", "0o10", "Infinity", "+", "１０", "١٠"):
        assert parse_number(value) is None
        assert parse_score_number(value) is None
    with pytest.raises(ValueError, match="function_id"):
        MetricRule("trait_1", 10, 1)


def _run_reference(payload, tmp_path):
    node = shutil.which("node")
    assert node is not None, "Node is required for the local GS source parity gate"
    # No credentials, NODE_OPTIONS, or provider secrets reach the subprocess.
    environment = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP") if key in os.environ}
    completed = subprocess.run(
        [node, str(ROOT / "gs_numeric_runner.js")], input=json.dumps(payload), text=True,
        capture_output=True, check=True, timeout=5, cwd=tmp_path, env=environment,
    )
    return json.loads(completed.stdout)
