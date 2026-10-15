"""Bounded synthetic core measurements; no account settings, Sheets or providers."""

import argparse
import json
import math
from pathlib import Path
import platform
import time

from adapters.process_memory import resident_bytes
from application.compute_budget import ComputeBudget, ComputeLimit
from application.healthcheck import calculate_healthcheck
from domain.execution import execution_checks
from domain.healthcheck import ELEMENTS, MetricRule, RoleRule
from domain.raid import RaidMember
from domain.raid_finalize import finalize_rounds
from domain.raid_planner import RaidProblem, select_rounds
from domain.snapshots import MemberKey, TrainingRecord, capture_config, capture_training


def profile_local(repeats=3, is_small=False):
    if type(repeats) is not int or not 1 <= repeats <= 5:
        raise ValueError("profile requires 1..5 repeats")
    members = tuple(MemberKey("area_mock", f"member_{i}") for i in range(3 if is_small else 30))
    roles = tuple(RoleRule(f"role_{i}", (ELEMENTS[i % 5],), "output",
        tuple(MetricRule(metric, 10, coefficient=1) for metric in ("skill_1", "skill_2", "skill_burst")), 1)
        for i in range(5 if is_small else 200))
    config = capture_config("common", "2026-10-15T00:00:00Z", "reference_mock", roles)
    training = capture_training("union_mock", "2026-10-15T00:00:00Z", "reference_mock",
        tuple(TrainingRecord(member, role.name_code, "owned", {metric.metric_id: 1 + (index % 10) for metric in role.metrics})
              for index, member in enumerate(members) for role in roles))
    raid_members = tuple(RaidMember(member, member.member_id, 800 + index) for index, member in enumerate(members))
    problem = RaidProblem(raid_members, (("100000000",) * 5,) * 3, ELEMENTS, 800, ("20",) * 5, (), (), (),
        (("inputs", "synthetic_profile"),), "profile-code-v1")
    measurements = []
    for kind in ("healthcheck", "raid"):
        samples = []
        for _ in range(repeats):
            probe = ProfileProbe()
            wall_start, cpu_start = time.perf_counter(), time.process_time()
            status, result_id = "succeeded", None
            try:
                with execution_checks(probe):
                    probe()
                    result = (calculate_healthcheck(config, training, members, {}, "profile-code-v1") if kind == "healthcheck" else
                        finalize_rounds(problem, select_rounds(problem)))
                    result_id = result.result_id if kind == "healthcheck" else result.draft_plan_id
                    probe()
            except ComputeLimit as error:
                status = error.code
            samples.append({"status": status, "result_id": result_id, "wall_seconds": time.perf_counter() - wall_start,
                "cpu_seconds": time.process_time() - cpu_start, "sampled_peak_rss_bytes": probe.peak,
                "checks": probe.budget.checks})
            if status != "succeeded":
                break
        measurements.append({"kind": kind, "samples": samples,
            "wall_p50_seconds": percentile([s["wall_seconds"] for s in samples], .5),
            "wall_p95_seconds": percentile([s["wall_seconds"] for s in samples], .95),
            "is_deterministic": len({s["result_id"] for s in samples}) == 1 and all(s["status"] == "succeeded" for s in samples)})
    return {"schema_version": 1, "is_mock": True, "platform": platform.system(), "python": platform.python_version(),
        "members": len(members), "roles": len(roles), "requested_repeats": repeats,
        "measurement_scope": "pure_core_plus_cooperative_checks_no_provider_billing", "measurements": measurements}


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)]


def main():
    parser = argparse.ArgumentParser(description="Bounded offline synthetic core profile")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("profile output must be a new file")
    report = profile_local(args.repeats)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(report, output, indent=2)
        output.write("\n")
    print(json.dumps({"members": report["members"], "roles": report["roles"], "deterministic": all(m["is_deterministic"] for m in report["measurements"])}))
    return 0 if all(m["is_deterministic"] for m in report["measurements"]) else 1


class ProfileProbe:
    def __init__(self):
        self.peak = 0
        self.budget = ComputeBudget(seconds=15, reserve_seconds=1, memory_limit_bytes=512 * 1024 * 1024, memory_reader=self.read_memory)

    def __call__(self):
        self.budget.check()

    def read_memory(self):
        used = resident_bytes()
        self.peak = max(self.peak, used)
        return used


if __name__ == "__main__":
    raise SystemExit(main())
