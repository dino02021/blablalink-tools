"""Map actual local JUnit outcomes to bounded specification evidence, never production signoff."""

import re
from xml.etree import ElementTree


def build_acceptance_report(junit_xml: bytes, matrix: dict) -> dict:
    if not isinstance(junit_xml, bytes) or len(junit_xml) > 16 * 1024 * 1024:
        raise ValueError("bounded local JUnit XML required")
    entries = matrix["requirements"]
    if (matrix.get("schema_version") != 1 or len(entries) != 22
            or {entry["id"] for entry in entries} != {f"A{i:02}" for i in range(1, 23)}):
        raise ValueError("complete A01-A22 evidence matrix required")
    observed = {}
    root = ElementTree.fromstring(junit_xml)
    for case in root.iter("testcase"):
        node = case.get("classname", "").replace(".", "/") + ".py::" + case.get("name", "").split("[", 1)[0]
        status = "failed" if case.find("failure") is not None or case.find("error") is not None else "skipped" if case.find("skipped") is not None else "passed"
        observed.setdefault(node, []).append(status)
    requirements = []
    for entry in entries:
        tests = entry["tests"]
        if (not tests or not entry["remaining"] or any(re.fullmatch(r"local_tests/test_[a-z0-9_]+\.py::test_[a-z0-9_]+", node) is None for node in tests)):
            raise ValueError("bounded test references and explicit remaining scope required")
        statuses = [status for node in tests for status in observed.get(node, ["not_run"])]
        status = "failed" if "failed" in statuses else "incomplete" if any(s != "passed" for s in statuses) else "local_evidence_passed"
        requirements.append({**entry, "evidence_status": status, "observed_cases": len(statuses)})
    all_statuses = [status for statuses in observed.values() for status in statuses]
    suite_status = "failed" if "failed" in all_statuses else "incomplete" if not all_statuses or any(s != "passed" for s in all_statuses) else "passed"
    return {"schema_version": 1, "scope": "local_mock_only", "is_production_accepted": False, "suite_status": suite_status,
        "observed_test_cases": sum(map(len, observed.values())),
        "local_evidence_passed": sum(r["evidence_status"] == "local_evidence_passed" for r in requirements),
        "requirements": requirements}
