from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import subprocess

from domain.healthcheck import ELEMENTS, MetricRule, RoleRule, score_union

ROOT = Path(__file__).resolve().parent


def test_full_cohort_role_weighted_total_and_rank_match_frozen_gs(tmp_path):
    cases = scoring_cases()
    actual = run_reference(cases, tmp_path)
    assert len(actual) == 50
    for case, reference in zip(cases, actual):
        rules = tuple(RoleRule(**{**row, "metrics": tuple(MetricRule(**m) for m in row["metrics"])}) for row in case["rules"])
        values = {(member, role): metrics for member, roles in case["values"].items() for role, metrics in roles.items()}
        result = score_union(tuple(case["members"]), rules, values)
        assert reference["isolated"]
        assert sorted(reference["roles"]) == sorted([s.member_id, s.key, s.value] for s in result.roles)
        assert sorted(reference["groups"]) == sorted([s.member_id, s.key, s.value, s.rank] for s in result.groups)


def scoring_cases():
    randomizer = random.Random(20261015)
    cases = []
    for index in range(50):
        members = [f"member_{i}" for i in range(1 + index % 9)]
        rules = []
        for role_index in range(1 + index % 8):
            types = ("output", "support") if role_index % 3 == 0 else (("support",) if role_index % 3 == 1 else ("output",))
            rule = RoleRule(f"role_{role_index}", (ELEMENTS[role_index % 5], ELEMENTS[(role_index + 1) % 5]), types[0],
                tuple(MetricRule(f"metric_{i}", None, randomizer.choice([0, 1, -1, .1, .5, 1.25, None, ""])) for i in range(4)),
                randomizer.choice([0, .1, .25, 1, 1.005, 10]), role_index != 7,
                additional_role_types=types[1:])
            rules.append(rule)
        options = [0, 1, 2, -1, .1, .3, "R0", "SR15", "T9", "5+", "", None]
        values = {member: {rule.name_code: {m.metric_id: randomizer.choice(options) for m in rule.metrics} for rule in rules} for member in members}
        if index % 7 == 0:
            # All-zero cohorts and exact ties are explicit, not incidental random coverage.
            values = {member: {rule.name_code: {m.metric_id: 0 for m in rule.metrics} for rule in rules} for member in members}
        cases.append({"members": members, "rules": [asdict(rule) for rule in rules], "values": values})
    return cases


def run_reference(cases, directory):
    fixture = ROOT / "fixtures/gs_scoring_reference.js"
    provenance = json.loads((ROOT / "fixtures/gs_scoring_provenance.json").read_text(encoding="utf-8"))
    assert hashlib.sha256(fixture.read_text(encoding="utf-8").encode()).hexdigest() == provenance["fixture_sha256"]
    payload = [{**case, "rules": [{**row, "tracked_types": list(RoleRule(**{**row,
        "metrics": tuple(MetricRule(**m) for m in row["metrics"])}).tracked_types)} for row in case["rules"]]} for case in cases]
    node = shutil.which("node")
    assert node, "local GS scoring parity requires Node"
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP") if key in os.environ}
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run([node, str(ROOT / "gs_scoring_runner.js")], input=json.dumps(payload),
        text=True, encoding="utf-8", capture_output=True, check=True, timeout=10, env=env, cwd=directory)
    return json.loads(result.stdout)
