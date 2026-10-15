"""Summarize an existing local JUnit run; never launch tests or external providers."""

import argparse
import json
from pathlib import Path

from application.acceptance import build_acceptance_report


def main():
    parser = argparse.ArgumentParser(description="Local mock requirement evidence from JUnit")
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    matrix_path = Path(__file__).resolve().parents[2] / "local_tests/acceptance_matrix.json"
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    if args.junit.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("bounded JUnit artifact required")
    report = build_acceptance_report(args.junit.read_bytes(), matrix)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
        output.write("\n")
    print(json.dumps({key: report[key] for key in ("suite_status", "observed_test_cases", "local_evidence_passed", "is_production_accepted")}))
    return 0 if report["suite_status"] == "passed" and report["local_evidence_passed"] == 22 else 1


if __name__ == "__main__":
    raise SystemExit(main())
