"""Read-only raid view, keyed by member identity; never rewrites designated inputs."""

from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_HALF_UP, localcontext
import hashlib

from domain.raid_finalize import FinalRaidPlan
from domain.raid_planner import RaidProblem, build_source_pool
from domain.raid import FixedInputs, estimate_damage_yi, prepare_fixed, simulate_stages
from domain.snapshots import MemberKey, canonical_json


@dataclass(frozen=True)
class ShotView:
    knife_id: str
    damage_true: str
    damage_yi: str
    stage: str
    is_history: bool


@dataclass(frozen=True)
class BossView:
    boss: int
    shots: tuple[ShotView, ...]

    @property
    def damage_text(self) -> str:
        return "\n".join(shot.damage_yi for shot in self.shots)

    @property
    def stage_text(self) -> str:
        stages = tuple(shot.stage for shot in self.shots)
        return stages[0] if stages and len(set(stages)) == 1 else "\n".join(stages)

    @property
    def is_all_history(self) -> bool:
        return bool(self.shots) and all(shot.is_history for shot in self.shots)


@dataclass(frozen=True)
class RaidMemberView:
    member: MemberKey
    display_name: str
    sync: int
    new_knife_count: int
    bosses: tuple[BossView, ...]


@dataclass(frozen=True)
class RaidView:
    draft_plan_id: str
    problem_id: str
    rows: tuple[RaidMemberView, ...]
    warnings: tuple[tuple[str, str], ...]

    @property
    def view_id(self) -> str:
        return hashlib.sha256(canonical_json(asdict(self)).encode()).hexdigest()


def build_raid_view(problem: RaidProblem, final: FinalRaidPlan) -> RaidView:
    if final.problem_id != problem.problem_id:
        raise ValueError("raid view input version mismatch")
    if simulate_stages(problem.members, problem.hp, problem.history, final.simulation.knives) != final.simulation:
        raise ValueError("raid simulation integrity differs")
    sources = {token.knife.knife_id: token.knife for token in build_source_pool(problem)}
    for knife in final.simulation.knives:
        source = sources.get(knife.knife_id)
        if source is None or source.member != knife.member or source.kind != knife.kind:
            raise ValueError("raid knife outside sealed source pool")
        if knife.kind == "fixed":
            if (source.boss, source.damage_true, source.slot, source.source_locked_stage) != (knife.boss, knife.damage_true, knife.slot, knife.source_locked_stage):
                raise ValueError("fixed knife input changed")
        else:
            member = next(m for m in problem.members if m.member == knife.member)
            if knife.damage_true != str(estimate_damage_yi(member.sync, problem.base_sync, problem.base_damage_yi[knife.boss - 1]) * 100000000):
                raise ValueError("automatic knife estimate changed")
    required_warnings = set()
    for member in problem.members:
        history = tuple(k for k in problem.history if k.member == member.member)
        preparation = prepare_fixed(member, dict(problem.fixed_inputs).get(member.member, FixedInputs(("", "", ""))),
                                    history, problem.base_sync, problem.base_damage_yi)
        required_warnings.update((member.member.key, code) for _, code in preparation.warnings)
        landed = sum(k.member == member.member and k.stage > 0 for k in final.simulation.knives)
        if landed < max(0, 3 - len(history)):
            required_warnings.add((member.member.key, "landing_shortage"))
    if not required_warnings <= set(final.warnings):
        raise ValueError("raid draft omits required warnings")
    rows = []
    with localcontext() as context:
        context.prec = 50
        for member in problem.members:
            new = tuple(k for k in final.simulation.knives if k.member == member.member and k.stage > 0)
            history = tuple(k for k in problem.history if k.member == member.member)
            knives = (*history, *new)
            bosses = []
            for boss in range(1, 6):
                shots = tuple(ShotView(k.knife_id, k.damage_true,
                    format((Decimal(k.damage_true) / Decimal(100000000)).quantize(Decimal(1), rounding=ROUND_HALF_UP), "f"),
                    ("", "I", "II", "III", "IV")[k.stage], k.kind == "history")
                    for k in sorted((k for k in knives if k.boss == boss), key=lambda k: (k.kind != "history", k.sequence, k.knife_id)))
                bosses.append(BossView(boss, shots))
            rows.append(RaidMemberView(member.member, member.display_name, member.sync, len(new), tuple(bosses)))
    rows.sort(key=lambda row: (row.new_knife_count == 0, row.sync, row.display_name, row.member.key))
    return RaidView(final.draft_plan_id, problem.problem_id, tuple(rows), final.warnings)
