"""Versioned offline raid source pools and round selection; no sheet intermediate reads."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from decimal import Decimal, localcontext
import hashlib

from domain.healthcheck import ELEMENTS
from domain.raid import FixedInputs, RaidKnife, RaidMember, RaidSimulation, estimate_damage_yi, prepare_fixed, simulate_stages
from domain.snapshots import MemberKey, canonical_json
from domain.execution import check_execution

FIRE_IRON = frozenset({"燃燒", "鐵甲"})


@dataclass(frozen=True)
class RaidProblem:
    members: tuple[RaidMember, ...]
    hp: tuple[tuple[str, ...], ...]
    boss_elements: tuple[str, ...]
    base_sync: int
    base_damage_yi: tuple[str, ...]
    history: tuple[RaidKnife, ...]
    fixed_inputs: tuple[tuple[MemberKey, FixedInputs], ...]
    ranks: tuple[tuple[MemberKey, tuple[tuple[str, int | None], ...]], ...]
    input_versions: tuple[tuple[str, str], ...]
    code_sha: str
    # Explicit cross-runtime tie contract; legacy localeCompare is a migration comparison item.
    element_tie_order: tuple[str, ...] = ELEMENTS
    overflow_policy: str = "legacy_effective"

    def __post_init__(self):
        for field in ("members", "boss_elements", "base_damage_yi", "history", "input_versions", "element_tie_order"):
            object.__setattr__(self, field, tuple(getattr(self, field)))
        object.__setattr__(self, "hp", tuple(tuple(row) for row in self.hp))
        object.__setattr__(self, "fixed_inputs", tuple((member, value) for member, value in self.fixed_inputs))
        object.__setattr__(self, "ranks", tuple((member, tuple(tuple(pair) for pair in values)) for member, values in self.ranks))
        object.__setattr__(self, "input_versions", tuple(sorted(tuple(pair) for pair in self.input_versions)))
        if not self.members or len({m.member for m in self.members}) != len(self.members):
            raise ValueError("unique nonempty raid roster required")
        if len(self.boss_elements) != 5 or any(e not in ELEMENTS for e in self.boss_elements):
            raise ValueError("five known boss elements required")
        if set(self.element_tie_order) != set(ELEMENTS) or len(self.element_tie_order) != 5:
            raise ValueError("complete deterministic element tie order required")
        if len(self.base_damage_yi) != 5:
            raise ValueError("five base damages required")
        if self.overflow_policy not in ("legacy_effective", "raw_damage"):
            raise ValueError("unsupported phase-three overflow policy")
        for damage in self.base_damage_yi:
            estimate_damage_yi(self.members[0].sync, self.base_sync, damage)
        identities = {m.member for m in self.members}
        for collection in (self.fixed_inputs, self.ranks):
            if len({m for m, _ in collection}) != len(collection) or any(m not in identities for m, _ in collection):
                raise ValueError("duplicate or foreign member input")
        for _, ranks in self.ranks:
            if len({e for e, _ in ranks}) != len(ranks) or any(e not in ELEMENTS or rank is not None and (type(rank) is not int or rank <= 0) for e, rank in ranks):
                raise ValueError("invalid output ranks")
        if (not self.input_versions or len(dict(self.input_versions)) != len(self.input_versions)
                or any(not isinstance(v, str) or not v for pair in self.input_versions for v in pair)
                or not isinstance(self.code_sha, str) or not self.code_sha):
            raise ValueError("fixed input and code versions required")
        simulate_stages(self.members, self.hp, self.history, ())

    @property
    def problem_id(self) -> str:
        return hashlib.sha256(canonical_json(asdict(self)).encode()).hexdigest()


@dataclass(frozen=True)
class SourceToken:
    knife: RaidKnife
    group_rank: int
    top_order: int


@dataclass(frozen=True)
class RoundSelection:
    problem_id: str
    source_pool: tuple[SourceToken, ...]
    selected: tuple[RaidKnife, ...]
    simulation: RaidSimulation
    unused_source_ids: tuple[str, ...]
    warnings: tuple[tuple[str, str], ...]
    algorithm_version: str = "round-selection-v1"

    @property
    def selection_id(self) -> str:
        return hashlib.sha256(canonical_json(asdict(self)).encode()).hexdigest()


def element_groups(problem: RaidProblem, member: MemberKey) -> tuple[tuple[str, ...], ...]:
    ranks = dict(dict(problem.ranks).get(member, ()))
    ordered = sorted(ELEMENTS, key=lambda e: (ranks.get(e) or 10 ** 30, problem.element_tie_order.index(e)))
    top = ordered[:3]
    if all(e in top for e in FIRE_IRON):
        demote = max(FIRE_IRON, key=lambda e: (ranks.get(e) or 10 ** 30, ordered.index(e)))
        ordered.remove(demote)
        ordered.insert(3, demote)
    blocked = set(_preparations(problem)[member].blocked_bosses)
    excluded = {problem.boss_elements[b - 1] for b in blocked}
    ordered = [e for e in ordered if e not in excluded]
    groups = [tuple(ordered[:3])] if ordered else []
    groups.extend((e,) for e in ordered[3:])
    fallback = tuple(dict.fromkeys(e for e in problem.boss_elements if all(e not in group for group in groups)))
    if fallback:
        groups.append(fallback)
    return tuple(groups)


def build_source_pool(problem: RaidProblem) -> tuple[SourceToken, ...]:
    preparations = _preparations(problem)
    tokens = []
    for member in sorted(problem.members, key=lambda m: (-m.sync, m.display_name, m.member.key)):
        check_execution()
        fixed = preparations[member.member]
        groups = element_groups(problem, member.member)
        for knife in fixed.knives:
            element = problem.boss_elements[knife.boss - 1]
            group = next((i for i, g in enumerate(groups) if element in g), len(groups))
            tokens.append(SourceToken(knife, group, 0))
        chosen = list(fixed.knives)
        available = max(0, 3 - sum(k.member == member.member for k in problem.history) - len(chosen))
        for slot in range(available):
            candidates = []
            for allow_conflict in (False, True):
                for group_rank, group in enumerate(groups):
                    for boss in range(1, 6):
                        element = problem.boss_elements[boss - 1]
                        if element not in group or not _is_legal_boss(problem, member.member, boss, chosen, fixed, allow_conflict, True):
                            continue
                        damage = estimate_damage_yi(member.sync, problem.base_sync, problem.base_damage_yi[boss - 1])
                        if damage > 0:
                            candidates.append((-damage, boss, group_rank, group.index(element)))
                    if candidates:
                        break
                if candidates:
                    break
            if not candidates:
                break
            negative_damage, boss, group_rank, order = min(candidates)
            knife = RaidKnife(f"auto:{member.member.key}:{slot}", member.member, boss, str(-negative_damage * 100000000))
            chosen.append(knife)
            tokens.append(SourceToken(knife, group_rank, order))
    return tuple(tokens)


def select_rounds(problem: RaidProblem) -> RoundSelection:
    """Three high-sync rounds, then bounded low-sync fallback. Stabilization is separate."""
    with localcontext() as context:
        context.prec = 50
        return _select_rounds(problem)


def _select_rounds(problem):
    pool = build_source_pool(problem)
    preparations = _preparations(problem)
    remaining = list(pool)
    selected = []
    sequence = sum(t.knife.kind == "fixed" for t in pool)
    ordered = sorted(problem.members, key=lambda m: (-m.sync, m.display_name, m.member.key))
    for round_index in range(1, 4):
        for member in ordered:
            choice = _choose(problem, member, selected, remaining, preparations[member.member], round_index, sequence, True)
            if choice:
                token, knife = choice
                selected.append(knife)
                remaining.remove(token)
                sequence += 1
    for round_index in range(4, 4 + len(pool)):
        before = len(selected)
        for member in sorted(problem.members, key=lambda m: (m.sync, m.display_name, m.member.key)):
            choice = _choose(problem, member, selected, remaining, preparations[member.member], round_index, sequence, False)
            if choice:
                token, knife = choice
                selected.append(knife)
                remaining.remove(token)
                sequence += 1
        if len(selected) == before or not remaining:
            break
    simulation = simulate_stages(problem.members, problem.hp, problem.history, tuple(selected))
    warnings = [(member.key, code) for member, prep in preparations.items() for _, code in prep.warnings]
    for member in problem.members:
        count = sum(k.member == member.member and k.stage > 0 for k in simulation.knives)
        expected = max(0, 3 - sum(k.member == member.member for k in problem.history))
        if count < expected:
            warnings.append((member.member.key, "landing_shortage"))
    return RoundSelection(problem.problem_id, pool, tuple(selected), simulation, tuple(t.knife.knife_id for t in remaining), tuple(warnings))


def _choose(problem, member, selected, remaining, fixed, round_index, sequence, strict):
    tokens = [t for t in remaining if t.knife.member == member.member]
    fixed_tokens = sorted((t for t in tokens if t.knife.kind == "fixed"), key=lambda t: t.knife.slot)
    if fixed_tokens:
        token = fixed_tokens[0]
        knife = replace(token.knife, round=round_index, sequence=sequence)
        simulate_stages(problem.members, problem.hp, problem.history, tuple((*selected, knife)))
        return token, knife
    auto_tokens = sorted((t for t in tokens if t.knife.kind == "auto" and (not strict or t.group_rank == 0)),
                         key=lambda t: (t.group_rank, t.top_order, -Decimal(t.knife.damage_true), t.knife.boss))
    if not auto_tokens:
        return None
    current = simulate_stages(problem.members, problem.hp, problem.history, tuple(selected))
    own = [k for k in selected if k.member == member.member]
    committed = [k for k in problem.history if k.member == member.member] + list(fixed.knives) + own
    has_coverage = any(problem.boss_elements[k.boss - 1] in FIRE_IRON for k in committed)
    for allow_conflict, require_coverage in ((False, True), (False, False), (True, False)):
        if require_coverage and has_coverage:
            continue
        options = []
        groups = element_groups(problem, member.member)
        for token in auto_tokens:
            stop = len(groups) if require_coverage or not strict else min(token.group_rank + 1, len(groups))
            for group_rank in range(token.group_rank, stop):
                for boss in range(1, 6):
                    element = problem.boss_elements[boss - 1]
                    if element not in groups[group_rank] or require_coverage and element not in FIRE_IRON:
                        continue
                    if boss not in current.deficit_bosses and not (boss == 5 and not current.deficit_bosses):
                        continue
                    if not _is_legal_boss(problem, member.member, boss, own, fixed, allow_conflict, not current.deficit_bosses):
                        continue
                    damage = estimate_damage_yi(member.sync, problem.base_sync, problem.base_damage_yi[boss - 1])
                    if damage <= 0:
                        continue
                    is_repeat = any(k.boss == boss for k in committed)
                    knife = RaidKnife(token.knife.knife_id, member.member, boss, str(damage * 100000000),
                        "auto", 4 if is_repeat else 0, is_repeat, round=round_index, sequence=sequence)
                    rerun = simulate_stages(problem.members, problem.hp, problem.history, tuple((*selected, knife)))
                    if not _is_front_no_worse(current, rerun) or overflow_for_policy(problem, rerun) > 0:
                        continue
                    is_deficit = boss in current.deficit_bosses
                    if strict and not is_deficit:
                        continue
                    target_hp = sum((Decimal(row[boss - 1]) for row in current.remaining_hp), Decimal(0))
                    score = (not is_deficit, group_rank, groups[group_rank].index(element), -target_hp,
                             Decimal(rerun.phase_three_overflow_true), -damage, boss)
                    options.append((score, token, knife))
        if options:
            _, token, knife = min(options, key=lambda item: item[0])
            return token, knife
    return None


def _is_legal_boss(problem, member, boss, chosen, fixed, allow_conflict, allow_tail_repeat):
    if boss in fixed.blocked_bosses:
        return False
    profile = next(m for m in problem.members if m.member == member)
    if estimate_damage_yi(profile.sync, problem.base_sync, problem.base_damage_yi[boss - 1]) <= 0:
        return False
    # All accepted fixed knives reserve their target even before their selection round.
    committed = [k for k in problem.history if k.member == member] + list(fixed.knives) + list(chosen)
    if any(k.boss == boss for k in committed) and not (boss == 5 and allow_tail_repeat):
        return False
    element = problem.boss_elements[boss - 1]
    if not allow_conflict and element in FIRE_IRON:
        return not any(problem.boss_elements[k.boss - 1] in FIRE_IRON - {element} for k in committed)
    return True


def _preparations(problem):
    inputs = dict(problem.fixed_inputs)
    return {member.member: prepare_fixed(member, inputs.get(member.member, FixedInputs(("", "", ""))),
        tuple(k for k in problem.history if k.member == member.member), problem.base_sync, problem.base_damage_yi)
        for member in problem.members}


def _is_front_no_worse(before, after):
    if len(after.deficit_bosses) > len(before.deficit_bosses):
        return False
    return len(after.deficit_bosses) < len(before.deficit_bosses) or _remaining_total(after) <= _remaining_total(before)


def _remaining_total(simulation):
    return sum((Decimal(value) for row in simulation.remaining_hp for value in row), Decimal(0))


def overflow_for_policy(problem: RaidProblem, simulation: RaidSimulation) -> Decimal:
    # Legacy GS sums already capped effectiveTrue, so its phase-three overflow gate is zero.
    return Decimal(simulation.phase_three_overflow_true) if problem.overflow_policy == "raw_damage" else Decimal(0)
