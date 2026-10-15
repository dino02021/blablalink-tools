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


@pytest.mark.parametrize('node',['local_tests/test_d1_cas.py::test_case','local_tests/test_case.py::test_round_2'])
def test_numeric_module_and_function_names_are_valid_exact_evidence(node):
    matrix=json.loads(MATRIX.read_text(encoding='utf-8'))
    for entry in matrix['requirements']:entry['tests']=[node]
    module,function=node.split('::')
    classname=module.removesuffix('.py').replace('/','.')
    xml=f'<testsuite><testcase classname="{classname}" name="{function}"/></testsuite>'.encode()
    report=build_acceptance_report(xml,matrix)
    assert report['local_evidence_passed']==22 and report['suite_status']=='passed'
    assert report['is_production_accepted'] is False


@pytest.mark.parametrize('node',['local_tests/../test_x.py::test_case','local_tests/test_case.py::test_case[param]','local_tests/test_case.py::test_case;extra'])
def test_numeric_name_support_does_not_allow_paths_parameters_or_partial_matches(node):
    matrix=json.loads(MATRIX.read_text(encoding='utf-8'))
    matrix['requirements'][0]['tests']=[node]
    with pytest.raises(ValueError,match='test references'):build_acceptance_report(b'<testsuite/>',matrix)
