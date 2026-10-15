from dataclasses import replace
from decimal import Decimal
import time

import pytest

from domain.healthcheck import ELEMENTS
from domain.raid import FixedInputs, RaidKnife, RaidMember
from domain.raid_planner import RaidProblem, build_source_pool, element_groups, select_rounds
from domain.snapshots import MemberKey


def test_source_pool_and_three_rounds_have_stable_unique_identity():
    problem = _problem()
    pool = build_source_pool(problem)
    assert len(pool) == 6 and len({t.knife.knife_id for t in pool}) == 6
    selected = select_rounds(problem)
    assert len(selected.selected) == 6
    assert [k.round for k in selected.selected] == [1, 1, 2, 2, 3, 3]
    assert selected.selected[0].member == problem.members[1].member  # high sync first
    assert selected.problem_id == problem.problem_id
    assert select_rounds(problem) == selected
    for member in problem.members:
        own = [k for k in selected.simulation.knives if k.member == member.member]
        elements = {problem.boss_elements[k.boss - 1] for k in own}
        assert elements.intersection({"燃燒", "鐵甲"})
        assert not {"燃燒", "鐵甲"} <= elements


def test_fixed_priority_and_original_input_are_preserved():
    problem = _problem()
    member = problem.members[0].member
    inputs = FixedInputs(("正四", "正一", "負二"), ("77", None, None, "55", "999"))
    problem = replace(problem, fixed_inputs=((member, inputs),))
    result = select_rounds(problem)
    own = [k for k in result.selected if k.member == member]
    assert [(k.kind, k.boss, k.slot) for k in own[:2]] == [("fixed", 4, 0), ("fixed", 1, 1)]
    assert own[0].damage_true == "5500000000"
    assert all(k.boss != 2 for k in own)
    assert dict(problem.fixed_inputs)[member] == inputs


def test_history_reduces_slots_and_is_not_retargeted():
    problem = _problem()
    member = problem.members[0].member
    history = (RaidKnife("history_one", member, 1, "1", "history", 1, True),
               RaidKnife("history_two", member, 2, "1", "history", 1, True))
    problem = replace(problem, history=history)
    result = select_rounds(problem)
    own = [k for k in result.selected if k.member == member]
    assert len(own) == 1 and own[0].boss not in (1, 2)
    assert problem.history == history


def test_front_cleared_fallback_fills_three_tail_knives_per_member():
    problem = replace(_problem(), hp=(("0",) * 5,) * 3)
    result = select_rounds(problem)
    assert len(result.simulation.knives) == 6
    assert all(k.boss == 5 and k.stage == 4 for k in result.simulation.knives)
    assert result.unused_source_ids == ()
    for member in problem.members:
        own = [k for k in result.simulation.knives if k.member == member.member]
        assert sum(k.is_locked for k in own) == 2


def test_reverse_boss_and_element_preference_exclusion():
    problem = _problem()
    member = problem.members[0].member
    problem = replace(problem, fixed_inputs=((member, FixedInputs(("負三", "負二", "負一"))),))
    groups = element_groups(problem, member)
    assert all(e not in groups[0] for e in problem.boss_elements[:3])
    result = select_rounds(problem)
    assert all(k.boss not in (1, 2, 3) for k in result.selected if k.member == member)


def test_input_version_and_code_changes_change_result_identity_even_same_plan():
    problem = _problem()
    original = select_rounds(problem)
    changed = select_rounds(replace(problem, code_sha="new_code", input_versions=(("ranks", "new_ranks"),)))
    assert original.selected == changed.selected
    assert original.selection_id != changed.selection_id


def test_same_display_name_does_not_merge_members():
    problem = _problem()
    problem = replace(problem, members=tuple(replace(m, display_name="同名") for m in problem.members))
    result = select_rounds(problem)
    assert len({k.member for k in result.selected}) == 2


def test_auto_selection_never_introduces_phase_three_overflow():
    problem = _problem()
    problem = replace(problem, hp=(("0",) * 5, ("0",) * 5, ("1",) * 5))
    result = select_rounds(problem)
    assert result.selected == ()
    assert Decimal(result.simulation.phase_three_overflow_true) == 0
    assert any(code == "landing_shortage" for _, code in result.warnings)


def test_small_roster_runtime_is_bounded():
    problem = _problem(12)
    started = time.monotonic()
    result = select_rounds(problem)
    assert len(result.selected) <= 36
    assert time.monotonic() - started < 15  # loose regression limit, not a hosted SLA


@pytest.mark.parametrize("field,value", [("base_sync", 0), ("boss_elements", ("unknown",) * 5),
                                        ("members", ()), ("element_tie_order", ("電擊",) * 5)])
def test_invalid_problem_rejected(field, value):
    with pytest.raises(ValueError):
        replace(_problem(), **{field: value})


def _problem(count=2):
    members = tuple(RaidMember(MemberKey("area_demo", f"member_{i}"), f"name_{i}", 100 + i) for i in range(count))
    return RaidProblem(members, (("1000000000000",) * 5,) * 3, ELEMENTS, 100, ("100",) * 5,
        (), (), tuple((m.member, tuple((element, i + 1) for i, element in enumerate(ELEMENTS))) for m in members),
        (("ranks", "rank_snapshot"), ("raid", "raid_config")), "code_demo")
