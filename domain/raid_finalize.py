"""Bounded offline stabilization, fixed priority, coverage swaps and final source refill."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from decimal import Decimal, localcontext
import hashlib

from domain.raid import RaidKnife, RaidSimulation, estimate_damage_yi, simulate_stages
from domain.raid_planner import (
    FIRE_IRON, RaidProblem, RoundSelection, _choose, _is_legal_boss, _preparations,
    _remaining_total, element_groups, overflow_for_policy,
)
from domain.snapshots import canonical_json


@dataclass(frozen=True)
class FinalRaidPlan:
    problem_id: str
    source_selection_id: str
    simulation: RaidSimulation
    unused_source_ids: tuple[str, ...]
    warnings: tuple[tuple[str, str], ...]
    changes: tuple[str, ...]
    algorithm_version: str = "round-finalization-v1"

    @property
    def draft_plan_id(self) -> str:
        return hashlib.sha256(canonical_json(asdict(self)).encode()).hexdigest()


def finalize_rounds(problem: RaidProblem, selection: RoundSelection) -> FinalRaidPlan:
    if selection.problem_id != problem.problem_id:
        raise ValueError("selection belongs to another immutable input")
    with localcontext() as context:
        context.prec = 50
        changes = []
        simulation = stabilize_plan(problem, selection.selected)
        if simulation != selection.simulation:
            changes.append("stabilization")
        simulation = _preserve_fixed(problem, simulation, changes)
        simulation = _balance_coverage(problem, simulation, changes)
        simulation = _recover_unlanded(problem, simulation, changes)
        simulation = _refill(problem, selection, simulation, changes)
        simulation = _balance_coverage(problem, simulation, changes)
        simulation = _recover_unlanded(problem, simulation, changes)
        simulation = _preserve_fixed(problem, simulation, changes)
        used = {k.knife_id for k in simulation.knives}
        unused = tuple(t.knife.knife_id for t in selection.source_pool if t.knife.knife_id not in used)
        warnings = [(member, code) for member, code in selection.warnings if code != "landing_shortage"]
        for member in problem.members:
            previous = sum(k.member == member.member for k in problem.history)
            count = sum(k.member == member.member and k.stage > 0 for k in simulation.knives)
            if count < max(0, 3 - previous):
                warnings.append((member.member.key, "landing_shortage"))
        return FinalRaidPlan(problem.problem_id, selection.selection_id, simulation, unused, tuple(warnings), tuple(changes))


def stabilize_plan(problem: RaidProblem, planned: tuple[RaidKnife, ...]) -> RaidSimulation:
    with localcontext() as context:
        context.prec = 50
        simulation = _simulate(problem, planned)
        seen = set()
        for _ in range(50):
            signature = _signature(simulation)
            if signature in seen:
                break
            seen.add(signature)
            updated = _reassign(problem, simulation) if simulation.deficit_bosses else _reduce_overflow(problem, simulation)
            if updated is None:
                break
            simulation = updated
        return simulation


def preserve_fixed_priority(problem: RaidProblem, simulation: RaidSimulation) -> RaidSimulation:
    with localcontext() as context:
        context.prec = 50
        return _preserve_fixed(problem, simulation, [])


def balance_element_coverage(problem: RaidProblem, simulation: RaidSimulation) -> RaidSimulation:
    with localcontext() as context:
        context.prec = 50
        return _balance_coverage(problem, simulation, [])


def recover_unlanded(problem: RaidProblem, simulation: RaidSimulation) -> RaidSimulation:
    with localcontext() as context:
        context.prec = 50
        return _recover_unlanded(problem, simulation, [])


def _reassign(problem, simulation):
    preparations = _preparations(problem)
    for member in sorted(problem.members, key=lambda m: (m.sync, m.display_name, m.member.key)):
        options = []
        replaceable = sorted((k for k in simulation.knives if k.member == member.member and k.kind == "auto" and not k.is_locked),
                             key=lambda k: (-k.round, -k.sequence))
        for knife in replaceable:
            candidates = _targets(problem, simulation, knife, preparations[member.member], False)
            for group_rank, boss in candidates:
                next_knife = _retarget(problem, simulation, knife, boss)
                rerun = _simulate(problem, _replace(simulation.knives, knife, next_knife))
                if not _is_front_improved(simulation, rerun) or overflow_for_policy(problem, rerun) > overflow_for_policy(problem, simulation):
                    continue
                score = (boss not in simulation.deficit_bosses, group_rank, len(rerun.deficit_bosses),
                         _remaining_total(rerun), -knife.round, member.member.key)
                options.append((score, rerun))
        if options:
            return min(options, key=lambda option: option[0])[1]
    return None


def _reduce_overflow(problem, simulation):
    current = overflow_for_policy(problem, simulation)
    if current <= 0:
        return None
    effective = dict(simulation.effective_true)
    overflow_bosses = {k.boss for k in simulation.knives if k.stage == 3 and Decimal(k.damage_true) > Decimal(effective[k.knife_id])}
    members = {m.member: m for m in problem.members}
    preparations = _preparations(problem)
    knives = sorted((k for k in simulation.knives if k.kind == "auto" and not k.is_locked and k.boss in overflow_bosses),
        key=lambda k: (members[k.member].sync, members[k.member].display_name, -k.round, k.sequence))
    for knife in knives:
        options = []
        for group_rank, boss in _targets(problem, simulation, knife, preparations[knife.member], False):
            if boss in overflow_bosses:
                continue
            rerun = _simulate(problem, _replace(simulation.knives, knife, _retarget(problem, simulation, knife, boss)))
            if not _is_no_worse(simulation, rerun) or overflow_for_policy(problem, rerun) >= current:
                continue
            options.append(((overflow_for_policy(problem, rerun), group_rank), rerun))
        if options:
            return min(options, key=lambda option: option[0])[1]
    return None


def _preserve_fixed(problem, simulation, changes):
    members = {m.member: m for m in problem.members}
    preparations = _preparations(problem)
    for _ in range(max(1, len(problem.members) * 3)):
        fixed = sorted((k for k in simulation.knives if k.kind == "fixed" and not k.is_locked and k.stage == 0 and k.boss != 5),
                       key=lambda k: (-members[k.member].sync, k.slot, members[k.member].display_name, k.member.key))
        accepted = None
        for target in fixed:
            effective = dict(simulation.effective_true)
            donors = sorted((k for k in simulation.knives if k.kind == "auto" and k.stage > 0 and not k.is_locked and k.boss == target.boss),
                key=lambda k: (members[k.member].sync >= members[target.member].sync, -members[k.member].sync,
                               Decimal(effective[k.knife_id]), members[k.member].display_name, k.member.key))
            target_bosses = [b for b in simulation.deficit_bosses if b != target.boss]
            if 5 not in target_bosses:
                target_bosses.append(5)
            for donor in donors:
                own = [k for k in simulation.knives if k.member == donor.member and k.knife_id != donor.knife_id]
                for allow_conflict in (False, True):
                    for boss in target_bosses:
                        if not _is_legal_boss(problem, donor.member, boss, own, preparations[donor.member], allow_conflict, True):
                            continue
                        rerun = _simulate(problem, _replace(simulation.knives, donor, _retarget(problem, simulation, donor, boss)))
                        if (not any(k.knife_id == target.knife_id and k.stage > 0 for k in rerun.knives)
                                or not _is_safe_exchange(problem, simulation, rerun)):
                            continue
                        accepted = rerun
                        break
                    if accepted:
                        break
                if accepted:
                    break
            if accepted:
                break
        if accepted is None:
            return simulation
        simulation = accepted
        changes.append("fixed_priority")
    return simulation


def _balance_coverage(problem, simulation, changes):
    preparations = _preparations(problem)
    seen = set()
    for _ in range(max(1, len(problem.members) * 3)):
        signature = _signature(simulation)
        if signature in seen:
            break
        seen.add(signature)
        coverage = _coverage(problem, simulation)
        double = [m for m in problem.members if coverage[m.member] >= 2]
        empty = [m for m in problem.members if coverage[m.member] == 0]
        accepted = None
        options = []
        for donor in sorted(double, key=lambda m: (m.sync, m.display_name, m.member.key)):
            for recipient in sorted(empty, key=lambda m: (m.sync, m.display_name, m.member.key)):
                donor_knives = [k for k in simulation.knives if k.member == donor.member and k.kind == "auto" and not k.is_locked and k.stage > 0
                                and problem.boss_elements[k.boss - 1] in FIRE_IRON]
                recipient_knives = [k for k in simulation.knives if k.member == recipient.member and k.kind == "auto" and not k.is_locked and k.stage > 0
                                    and problem.boss_elements[k.boss - 1] not in FIRE_IRON]
                for first in donor_knives:
                    for second in recipient_knives:
                        others_first = [k for k in simulation.knives if k.member == first.member and k.knife_id != first.knife_id]
                        others_second = [k for k in simulation.knives if k.member == second.member and k.knife_id != second.knife_id]
                        if (not _is_legal_boss(problem, first.member, second.boss, others_first, preparations[first.member], False, False)
                                or not _is_legal_boss(problem, second.member, first.boss, others_second, preparations[second.member], False, False)):
                            continue
                        knives = _replace(simulation.knives, first, _retarget(problem, simulation, first, second.boss))
                        knives = _replace(knives, second, _retarget(problem, simulation, second, first.boss))
                        rerun = _simulate(problem, knives)
                        new_coverage = _coverage(problem, rerun)
                        if (not _is_safe_exchange(problem, simulation, rerun) or new_coverage[recipient.member] == 0
                                or sum(v == 0 for v in new_coverage.values()) >= len(empty)):
                            continue
                        score = (_element_rank(problem, donor.member, second.boss) + _element_rank(problem, recipient.member, first.boss),
                                 recipient.sync, donor.sync, first.boss, second.boss, recipient.member.key, donor.member.key)
                        options.append((score, rerun))
        if options:
            accepted = min(options, key=lambda option: option[0])[1]
        if accepted is None:
            break
        simulation = accepted
        changes.append("element_coverage_swap")
    return simulation


def _recover_unlanded(problem, simulation, changes):
    preparations = _preparations(problem)
    members = {m.member: m for m in problem.members}
    for _ in range(max(1, len(problem.members) * 3)):
        knives = sorted((k for k in simulation.knives if k.kind == "auto" and not k.is_locked and k.stage == 0),
                        key=lambda k: (members[k.member].sync, members[k.member].display_name, k.sequence))
        accepted = None
        for knife in knives:
            member = members[knife.member]
            own = [k for k in simulation.knives if k.member == knife.member and k.knife_id != knife.knife_id]
            for allow_conflict in (False, True):
                bosses = [boss for boss in range(1, 6) if boss != knife.boss
                    and (boss in simulation.deficit_bosses or boss == 5 and not simulation.deficit_bosses)
                    and _is_legal_boss(problem, knife.member, boss, own, preparations[knife.member], allow_conflict, not simulation.deficit_bosses)]
                bosses.sort(key=lambda boss: (-estimate_damage_yi(member.sync, problem.base_sync, problem.base_damage_yi[boss - 1]), boss))
                for boss in bosses:
                    rerun = _simulate(problem, _replace(simulation.knives, knife, _retarget(problem, simulation, knife, boss)))
                    if _landed(rerun) > _landed(simulation) and _is_safe_exchange(problem, simulation, rerun):
                        accepted = rerun
                        break
                if accepted:
                    break
            if accepted:
                break
        if accepted is None:
            break
        simulation = accepted
        changes.append("unlanded_recovery")
    return simulation


def _refill(problem, selection, simulation, changes):
    used = {k.knife_id for k in simulation.knives}
    remaining = [t for t in selection.source_pool if t.knife.knife_id not in used]
    selected = list(simulation.knives)
    preparations = _preparations(problem)
    sequence = max((k.sequence for k in selected), default=0) + 1
    for round_index in range(4, 7):
        before = len(selected)
        for member in sorted(problem.members, key=lambda m: (m.sync, m.display_name, m.member.key)):
            chosen = _choose(problem, member, selected, remaining, preparations[member.member], round_index, sequence, False)
            if chosen:
                token, knife = chosen
                selected.append(knife)
                remaining.remove(token)
                sequence += 1
        if len(selected) == before:
            break
    if len(selected) > len(simulation.knives):
        changes.append("source_refill")
        simulation = stabilize_plan(problem, tuple(selected))
        simulation = _balance_coverage(problem, simulation, changes)
        simulation = _recover_unlanded(problem, simulation, changes)
    return simulation


def _targets(problem, simulation, knife, preparation, allow_tail):
    own = [k for k in simulation.knives if k.member == knife.member and k.knife_id != knife.knife_id]
    groups = element_groups(problem, knife.member)
    for conflict in (False, True):
        candidates = []
        for group_rank, group in enumerate(groups):
            for boss in range(1, 6):
                if boss == knife.boss or problem.boss_elements[boss - 1] not in group:
                    continue
                if boss not in simulation.deficit_bosses and not (boss == 5 and not simulation.deficit_bosses):
                    continue
                if _is_legal_boss(problem, knife.member, boss, own, preparation, conflict, allow_tail and not simulation.deficit_bosses):
                    candidates.append((group_rank, boss))
        if candidates:
            return tuple(candidates)
    return ()


def _retarget(problem, simulation, knife, boss):
    member = next(m for m in problem.members if m.member == knife.member)
    damage = estimate_damage_yi(member.sync, problem.base_sync, problem.base_damage_yi[boss - 1])
    committed = [k for k in (*problem.history, *simulation.knives) if k.member == knife.member and k.knife_id != knife.knife_id]
    is_repeat = boss == 5 and any(k.boss == boss for k in committed)
    return replace(knife, boss=boss, damage_true=str(damage * 100000000), stage=4 if is_repeat else 0,
                   is_locked=is_repeat, source_locked_stage=4 if is_repeat else 0)


def _is_safe_exchange(problem, before, after):
    return (_is_no_worse(before, after) and _landed(after) >= _landed(before)
            and _landed_fixed(before) <= _landed_fixed(after)
            and overflow_for_policy(problem, after) <= overflow_for_policy(problem, before))


def _coverage(problem, simulation):
    return {m.member: len({problem.boss_elements[k.boss - 1] for k in (*problem.history, *simulation.knives)
                           if k.member == m.member and k.stage > 0 and problem.boss_elements[k.boss - 1] in FIRE_IRON})
            for m in problem.members}


def _landed(simulation):
    return sum(k.stage > 0 for k in simulation.knives)


def _landed_fixed(simulation):
    return {k.knife_id for k in simulation.knives if k.kind == "fixed" and k.stage > 0}


def _is_no_worse(before, after):
    return len(after.deficit_bosses) <= len(before.deficit_bosses) and _remaining_total(after) <= _remaining_total(before)


def _is_front_improved(before, after):
    return (len(after.deficit_bosses) < len(before.deficit_bosses)
            or len(after.deficit_bosses) == len(before.deficit_bosses) and _remaining_total(after) < _remaining_total(before))


def _simulate(problem, knives):
    return simulate_stages(problem.members, problem.hp, problem.history, tuple(knives))


def _replace(knives, old, new):
    return tuple(new if k.knife_id == old.knife_id else k for k in knives)


def _signature(simulation):
    return tuple((k.knife_id, k.boss, k.source_locked_stage, k.is_locked) for k in simulation.knives)


def _element_rank(problem, member, boss):
    ordered = tuple(e for group in element_groups(problem, member) for e in group)
    return ordered.index(problem.boss_elements[boss - 1])
