from dataclasses import replace
from decimal import Decimal

import pytest

from domain.healthcheck import ELEMENTS
from domain.raid import FixedInputs, RaidKnife, RaidMember, simulate_stages
from domain.raid_planner import RaidProblem, overflow_for_policy, select_rounds
from domain.raid_finalize import balance_element_coverage, finalize_rounds, preserve_fixed_priority, recover_unlanded, stabilize_plan
from domain.snapshots import MemberKey


def test_high_sync_fixed_displaces_auto_into_repeated_tail_without_changing_fixed():
    problem = _problem()
    low, high = [m.member for m in problem.members]
    problem = replace(problem, hp=(("0",) * 5, ("0",) * 5, ("1000000000", "0", "0", "0", "1000000000")),
        fixed_inputs=((high, FixedInputs(("正一", "", ""))),), base_damage_yi=("10",) * 5)
    planned = (RaidKnife("low_one", low, 1, "1000000000", sequence=1),
               RaidKnife("low_five", low, 5, "1000000000", sequence=2),
               RaidKnife("fixed_high", high, 1, "8000000000", "fixed", slot=0, sequence=3))
    initial = simulate_stages(problem.members, problem.hp, (), planned)
    assert initial.knives[2].stage == 0
    prioritized = preserve_fixed_priority(problem, initial)
    assert prioritized.knives[2].boss == 1 and prioritized.knives[2].stage == 3
    own = prioritized.knives[:2]
    assert {k.boss for k in own} == {5} and {k.stage for k in own} == {3, 4}
    assert all(k.stage > 0 for k in prioritized.knives)
    assert planned[2].stage == 0


def test_unlanded_auto_recovers_only_into_tail_after_front_clears():
    problem = replace(_problem(), hp=(("0",) * 5,) * 3)
    low = problem.members[0].member
    knives = (RaidKnife("unlanded", low, 1, "1000000000"),)
    initial = simulate_stages(problem.members, problem.hp, (), knives)
    result = recover_unlanded(problem, initial)
    assert result.knives[0].boss == 5 and result.knives[0].stage == 4


def test_recovery_never_retargets_locked_tail_while_front_incomplete():
    problem = _problem()
    low = problem.members[0].member
    locked = RaidKnife("locked", low, 5, "1000000000", "auto", 4, True)
    initial = simulate_stages(problem.members, problem.hp, (), (locked,))
    result = recover_unlanded(problem, initial)
    assert result == initial and result.knives[0].source_locked_stage == 4


def test_pair_swap_improves_fire_iron_coverage_without_moving_fixed_or_history():
    problem = replace(_problem(), members=tuple(replace(m, sync=100) for m in _problem().members))
    low, high = [m.member for m in problem.members]
    knives = (RaidKnife("burn", low, 3, "1000000000"), RaidKnife("iron", low, 5, "1000000000"),
              RaidKnife("water", high, 2, "1000000000"))
    initial = simulate_stages(problem.members, problem.hp, (), knives)
    result = balance_element_coverage(problem, initial)
    for member in (low, high):
        elements = {problem.boss_elements[k.boss - 1] for k in result.knives if k.member == member}
        assert len(elements.intersection({"燃燒", "鐵甲"})) == 1
    assert {k.knife_id for k in result.knives} == {k.knife_id for k in initial.knives}


def test_stabilization_improves_deficit_with_existing_auto_knife():
    problem = _problem()
    low = problem.members[0].member
    problem = replace(problem, hp=(("1", "1000000000", "0", "0", "0"), ("0",) * 5, ("0",) * 5))
    knife = RaidKnife("move", low, 1, "1000000000")
    result = stabilize_plan(problem, (knife,))
    assert result.knives[0].boss == 2
    assert sum(Decimal(v) for row in result.remaining_hp for v in row) == 1


def test_legacy_overflow_gate_and_raw_waste_are_distinct_versioned_policies():
    problem = replace(_problem(), hp=(("0",) * 5, ("0",) * 5, ("1",) * 5))
    selection = select_rounds(problem)
    assert selection.selected
    assert Decimal(selection.simulation.phase_three_overflow_true) > 0
    assert overflow_for_policy(problem, selection.simulation) == 0
    strict = replace(problem, overflow_policy="raw_damage")
    assert strict.problem_id != problem.problem_id
    assert select_rounds(strict).selected == ()


def test_finalization_is_deterministic_and_bound_to_versions():
    problem = _problem()
    selection = select_rounds(problem)
    final = finalize_rounds(problem, selection)
    assert final.problem_id == problem.problem_id
    assert finalize_rounds(problem, selection) == final
    assert len(final.draft_plan_id) == 64
    with pytest.raises(ValueError):
        finalize_rounds(replace(problem, code_sha="different"), selection)


def _problem():
    members = (RaidMember(MemberKey("area", "low"), "low", 100), RaidMember(MemberKey("area", "high"), "high", 200))
    return RaidProblem(members, (("1000000000000",) * 5,) * 3, ELEMENTS, 100, ("10",) * 5, (), (),
        tuple((m.member, tuple((e, i + 1) for i, e in enumerate(ELEMENTS))) for m in members), (("inputs", "demo"),), "code_demo")
