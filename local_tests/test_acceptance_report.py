import copy
import json
from pathlib import Path

import pytest

from application.acceptance import build_acceptance_report

MATRIX = Path(__file__).with_name("acceptance_matrix.json")


def test_missing_results_are_incomplete_and_never_production_accepted():
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    report = build_acceptance_report(b"<testsuite/>", matrix)
    assert report["local_evidence_passed"] == 0
    assert report["suite_status"] == "incomplete"
    assert all(row["evidence_status"] == "incomplete" for row in report["requirements"])
    assert report["is_production_accepted"] is False


@pytest.mark.parametrize("outcome,expected", [("", "local_evidence_passed"), ("<failure/>", "failed"), ("<skipped/>", "incomplete")])
def test_parameterized_outcomes_remain_visible(outcome, expected):
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))
    matrix = copy.deepcopy(matrix)
    for entry in matrix["requirements"]:
        entry["tests"] = ["local_tests/test_demo.py::test_case"]
    xml = ('<testsuite><testcase classname="local_tests.test_demo" name="test_case[first]"/>'
        '<testcase classname="local_tests.test_demo" name="test_case[second]">' + outcome + '</testcase></testsuite>').encode()
    report = build_acceptance_report(xml, matrix)
    assert all(entry["evidence_status"] == expected for entry in report["requirements"])
    assert report["suite_status"] == {"local_evidence_passed": "passed", "failed": "failed", "incomplete": "incomplete"}[expected]
    assert report["is_production_accepted"] is False
