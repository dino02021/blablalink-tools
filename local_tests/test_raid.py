from decimal import localcontext

import pytest

from domain.raid import FixedInputs, RaidKnife, RaidMember, estimate_damage_yi, parse_history, prepare_fixed, simulate_stages
from domain.snapshots import MemberKey

MEMBER = MemberKey("area_demo", "member_demo")
OTHER = MemberKey("area_demo", "other_demo")


def test_history_multiline_stage_broadcast_and_invalid_lines():
    knives, warnings = parse_history(MEMBER, 5, "100000000\n200000000\ninvalid", "IV")
    assert len(knives) == 2 and [k.stage for k in knives] == [4, 4]
    assert all(k.kind == "history" and k.is_locked for k in knives)
    assert warnings == ("invalid_history_line:2",)
    knives, warnings = parse_history(MEMBER, 1, "100\n200", "I\nII")
    assert [k.stage for k in knives] == [1, 2] and not warnings


def test_estimate_and_fixed_override_use_legacy_positive_rounding():
    assert estimate_damage_yi(150, 100, "100") == 338
    member = RaidMember(MEMBER, "同名", 100)
    inputs = FixedInputs((" 正4 ", "負二", "正四"), (None, 999, None, "20.5", None))
    fixed = prepare_fixed(member, inputs, (), 100, (100,) * 5)
    assert fixed.knives[0].boss == 4 and fixed.knives[0].damage_true == "2100000000"
    assert fixed.blocked_bosses == (2,)
    assert fixed.warnings == ((2, "duplicate_boss_input"),)
    assert fixed.original == inputs  # invalid slots and unused overrides are never cleared


def test_repeated_fixed_five_locks_only_repeated_knives_to_tail():
    inputs = FixedInputs(("正五", "正5", "正五"))
    fixed = prepare_fixed(RaidMember(MEMBER, "name", 100), inputs, (), 100, (100,) * 5)
    assert [k.stage for k in fixed.knives] == [0, 4, 4]
    assert [k.slot for k in fixed.knives] == [0, 1, 2]
    history = parse_history(MEMBER, 5, "100", "I")[0]
    fixed = prepare_fixed(RaidMember(MEMBER, "name", 100), inputs, history, 100, (100,) * 5)
    assert len(fixed.knives) == 2 and all(k.stage == 4 for k in fixed.knives)
    assert fixed.warnings[-1] == (2, "member_knife_limit")


def test_history_collision_and_negative_input_conflict_keep_original():
    member = RaidMember(MEMBER, "name", 100)
    history = parse_history(MEMBER, 1, "100", "I")[0]
    original = FixedInputs(("正一", "負五", "正五"))
    fixed = prepare_fixed(member, original, history, 100, (100,) * 5)
    assert fixed.knives == () and fixed.blocked_bosses == (5,)
    assert fixed.warnings == ((0, "boss_already_in_history"), (2, "duplicate_boss_input"))
    assert fixed.original.slots == original.slots


def test_damage_overflow_does_not_carry_to_next_phase():
    hp = (("100", "0", "0", "0", "0"), ("100", "0", "0", "0", "0"), ("100", "0", "0", "0", "0"))
    knife = RaidKnife("auto_demo", MEMBER, 1, "1000")
    result = simulate_stages((RaidMember(MEMBER, "name", 100),), hp, (), (knife,))
    assert result.knives[0].stage == 1
    assert result.remaining_hp[1][0] == "100" and result.remaining_hp[2][0] == "100"
    assert dict(result.effective_true)[knife.knife_id] == "100"


def test_history_is_immutable_deducts_its_stage_and_tail_waits_for_all_bosses():
    hp = (("0", "0", "0", "0", "100"), ("0",) * 5, ("0", "100", "0", "0", "0"))
    history = (RaidKnife("history", MEMBER, 5, "1000", "history", 1, True),)
    tail = RaidKnife("fixed_tail", MEMBER, 5, "500", "fixed", 4, True)
    members = (RaidMember(MEMBER, "name", 100), RaidMember(OTHER, "name", 110))
    waiting = simulate_stages(members, hp, history, (tail,))
    assert waiting.knives[0].stage == 0 and dict(waiting.effective_true)[tail.knife_id] == "0"
    assert waiting.knives[0].is_locked and waiting.knives[0].source_locked_stage == 4
    clear = RaidKnife("auto_clear", OTHER, 2, "100")
    landed = simulate_stages(members, hp, history, (tail, clear))
    assert landed.knives[0].stage == 4
    assert dict(landed.effective_true)[tail.knife_id] == "500"
    assert tail.stage == 4 and tail.is_locked  # source lock survives an unlanded simulation
    assert landed.phase_three_overflow_true == "0"
    assert simulate_stages(members, hp, history, (waiting.knives[0], clear)) == landed


def test_low_sync_stage_order_and_precise_large_history_damage():
    hp = (("999999999999999999999999.000001", "0", "0", "0", "0"), ("1", "0", "0", "0", "0"), ("1", "0", "0", "0", "0"))
    members = (RaidMember(MEMBER, "same", 200), RaidMember(OTHER, "same", 100))
    planned = (RaidKnife("high", MEMBER, 1, "999999999999999999999999"), RaidKnife("low", OTHER, 1, "0.000001"))
    with localcontext() as context:
        context.prec = 6  # global caller precision must not corrupt fixed-point HP arithmetic
        result = simulate_stages(members, hp, (), planned)
    assert result.remaining_hp[0][0] == "0"
    assert dict(result.effective_true)["low"] == "0.000001"


def test_same_boss_repetition_and_more_than_three_planned_knives_rejected():
    member = RaidMember(MEMBER, "name", 100)
    knives = (RaidKnife("a", MEMBER, 1, "1"), RaidKnife("b", MEMBER, 1, "1"))
    with pytest.raises(ValueError, match="repeat"):
        simulate_stages((member,), ((1,) * 5,) * 3, (), knives)
    knives = tuple(RaidKnife(str(b), MEMBER, b, "1") for b in range(1, 5))
    with pytest.raises(ValueError, match="slots"):
        simulate_stages((member,), ((1,) * 5,) * 3, (), knives)


@pytest.mark.parametrize("damage", [True, float("nan"), "Infinity", "-1", "0", "0x10", "1e100", "1.1234567"])
def test_invalid_exact_damage_rejected(damage):
    with pytest.raises(ValueError):
        RaidKnife("demo", MEMBER, 1, damage)
