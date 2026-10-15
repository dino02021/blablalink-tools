"""Restore pinned raid input objects without evaluating code or reading external data."""

from dataclasses import asdict

from application.tasks import StateConflict
from domain.raid import FixedInputs, RaidKnife, RaidMember
from domain.raid_planner import RaidProblem
from domain.snapshots import MemberKey, canonical_json


def restore_problem(data: dict) -> RaidProblem:
    problem = RaidProblem(**{**data,
        "members": tuple(RaidMember(**{**row, "member": MemberKey(**row["member"])}) for row in data["members"]),
        "history": tuple(RaidKnife(**{**row, "member": MemberKey(**row["member"])}) for row in data["history"]),
        "fixed_inputs": tuple((MemberKey(**member), FixedInputs(**fixed)) for member, fixed in data["fixed_inputs"]),
        "ranks": tuple((MemberKey(**member), values) for member, values in data["ranks"])})
    if canonical_json(asdict(problem)) != canonical_json(data):
        raise StateConflict("raid input is not in canonical model representation")
    return problem
