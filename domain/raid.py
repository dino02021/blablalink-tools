"""Offline raid inputs and stage simulation. Full round selection lives above this layer."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation, localcontext

from domain.snapshots import MemberKey
from domain.execution import check_execution

YI = Decimal(100000000)


@dataclass(frozen=True)
class RaidMember:
    member: MemberKey
    display_name: str
    sync: int

    def __post_init__(self):
        if not isinstance(self.member, MemberKey) or not isinstance(self.display_name, str):
            raise ValueError("member identity and label required")
        if type(self.sync) is not int or not 0 < self.sync <= 100000:
            raise ValueError("positive bounded sync required")


@dataclass(frozen=True)
class RaidKnife:
    knife_id: str
    member: MemberKey
    boss: int
    damage_true: str
    kind: str = "auto"
    stage: int = 0
    is_locked: bool = False
    slot: int = 0
    round: int = 0
    sequence: int = 0
    source_locked_stage: int = 0

    def __post_init__(self):
        if not isinstance(self.knife_id, str) or not self.knife_id or not isinstance(self.member, MemberKey):
            raise ValueError("knife identity required")
        if type(self.boss) is not int or not 1 <= self.boss <= 5:
            raise ValueError("boss outside 1..5")
        if self.kind not in ("history", "fixed", "auto") or type(self.stage) is not int or not 0 <= self.stage <= 4:
            raise ValueError("invalid knife kind or stage")
        if type(self.is_locked) is not bool or self.kind == "history" and (not self.is_locked or self.stage == 0):
            raise ValueError("history requires locked stage")
        if self.stage == 4 and self.boss != 5:
            raise ValueError("stage IV only supports boss five")
        if type(self.source_locked_stage) is not int or not 0 <= self.source_locked_stage <= 4:
            raise ValueError("invalid source stage")
        source_stage = self.source_locked_stage or (self.stage if self.is_locked else 0)
        if self.is_locked and source_stage == 0:
            raise ValueError("locked stage required")
        if source_stage == 4 and self.boss != 5:
            raise ValueError("source tail stage only supports boss five")
        object.__setattr__(self, "source_locked_stage", source_stage)
        for value in (self.slot, self.round, self.sequence):
            if type(value) is not int or value < 0:
                raise ValueError("nonnegative input order required")
        object.__setattr__(self, "damage_true", decimal_text(_positive(self.damage_true)))


@dataclass(frozen=True)
class FixedInputs:
    slots: tuple[str, ...]
    damage_overrides_yi: tuple[str | int | None, ...] = (None,) * 5

    def __post_init__(self):
        slots, overrides = tuple(self.slots), tuple(self.damage_overrides_yi)
        if len(slots) != 3 or len(overrides) != 5 or any(not isinstance(v, str) for v in slots):
            raise ValueError("three slots and five damage inputs required")
        if any(v is not None and type(v) not in (str, int) for v in overrides):
            raise ValueError("overrides must be explicit decimal text or integers")
        object.__setattr__(self, "slots", slots)
        object.__setattr__(self, "damage_overrides_yi", overrides)


@dataclass(frozen=True)
class FixedPreparation:
    knives: tuple[RaidKnife, ...]
    blocked_bosses: tuple[int, ...]
    warnings: tuple[tuple[int, str], ...]
    original: FixedInputs


@dataclass(frozen=True)
class RaidSimulation:
    knives: tuple[RaidKnife, ...]
    effective_true: tuple[tuple[str, str], ...]
    remaining_hp: tuple[tuple[str, ...], ...]
    deficit_bosses: tuple[int, ...]
    phase_three_overflow_true: str


def decimal_text(value: Decimal) -> str:
    return format(value, "f")


def estimate_damage_yi(sync: int, base_sync: int, base_damage_yi) -> int:
    if type(sync) is not int or type(base_sync) is not int or not 0 < sync <= 100000 or not 0 < base_sync <= 100000:
        raise ValueError("positive bounded sync required")
    damage = float(_positive(base_damage_yi))
    result = (math.pow(sync, 3) / math.pow(base_sync, 3)) * damage
    if not math.isfinite(result) or result > 2 ** 53:
        raise ValueError("estimate exceeds exact integer range")
    return _round_positive(result)


def parse_history(member: MemberKey, boss: int, damage_cell, stage_cell) -> tuple[tuple[RaidKnife, ...], tuple[str, ...]]:
    damages, stages = _lines(damage_cell), _lines(stage_cell)
    knives, warnings = [], []
    for index, damage in enumerate(damages):
        raw_stage = stages[0] if len(stages) == 1 else stages[index] if index < len(stages) else ""
        stage = {"I": 1, "II": 2, "III": 3, "IV": 4, "1": 1, "2": 2, "3": 3, "4": 4}.get(raw_stage, 0)
        try:
            knives.append(RaidKnife(f"history:{member.key}:{boss}:{index}", member, boss, damage,
                                    "history", stage, True, sequence=index))
        except ValueError:
            warnings.append(f"invalid_history_line:{index}")
    if len(stages) not in (0, 1, len(damages)):
        warnings.append("history_line_count_differs")
    return tuple(knives), tuple(warnings)


def prepare_fixed(member: RaidMember, inputs: FixedInputs, history: tuple[RaidKnife, ...],
                  base_sync: int, base_damage_yi: tuple) -> FixedPreparation:
    check_execution()
    if len(base_damage_yi) != 5 or any(k.member != member.member or k.kind != "history" for k in history):
        raise ValueError("history and boss damage scope differs")
    seen, blocked, knives, warnings = {}, [], [], []
    for slot, token in enumerate(inputs.slots):
        text = re.sub(r"\s+", "", token).translate(str.maketrans("12345", "一二三四五"))
        if not text:
            continue
        if not re.fullmatch(r"[正負][一二三四五]", text):
            warnings.append((slot, "invalid_fixed_token"))
            continue
        boss, kind = "一二三四五".index(text[1]) + 1, "fixed" if text[0] == "正" else "reverse"
        repeated = boss in seen
        if repeated and not (boss == 5 and kind == "fixed" and seen[boss] == "fixed"):
            warnings.append((slot, "duplicate_boss_input"))
            continue
        seen[boss] = kind
        if len(history) + len(knives) >= 3:
            warnings.append((slot, "member_knife_limit"))
            continue
        historical = any(k.boss == boss for k in history)
        if historical and not (boss == 5 and kind == "fixed"):
            warnings.append((slot, "boss_already_in_history"))
            continue
        if kind == "reverse":
            blocked.append(boss)
            continue
        override = inputs.damage_overrides_yi[boss - 1]
        try:
            damage = _round_positive(float(_positive(override)))
        except (ValueError, TypeError):
            damage = 0
            if override not in (None, "", 0, "0"):
                warnings.append((slot, "invalid_override_fallback"))
        if not damage:
            damage = estimate_damage_yi(member.sync, base_sync, base_damage_yi[boss - 1])
        if damage <= 0:
            warnings.append((slot, "nonpositive_estimate"))
            continue
        is_tail = boss == 5 and (repeated or historical)
        knives.append(RaidKnife(f"fixed:{member.member.key}:{slot}", member.member, boss, str(damage * 100000000),
                                "fixed", 4 if is_tail else 0, is_tail, slot, sequence=slot))
    return FixedPreparation(tuple(knives), tuple(blocked), tuple(warnings), inputs)


def simulate_stages(members: tuple[RaidMember, ...], hp: tuple[tuple, ...],
                    history: tuple[RaidKnife, ...], planned: tuple[RaidKnife, ...]) -> RaidSimulation:
    check_execution()
    with localcontext() as context:
        context.prec = 50
        return _simulate_stages(members, hp, history, planned)


def _simulate_stages(members, hp, history, planned):
    if len(hp) != 3 or any(len(row) != 5 for row in hp):
        raise ValueError("three finite phases with five bosses required")
    remaining = [[_nonnegative(value) for value in row] for row in hp]
    member_map = {m.member: m for m in members}
    all_knives = (*history, *planned)
    if len(member_map) != len(members) or len({k.knife_id for k in all_knives}) != len(all_knives):
        raise ValueError("duplicate member or knife identity")
    if any(k.member not in member_map for k in all_knives) or any(k.kind != "history" for k in history) or any(k.kind == "history" for k in planned):
        raise ValueError("knife outside source scope")
    for member in members:
        check_execution()
        previous = sum(k.member == member.member for k in history)
        new = sum(k.member == member.member for k in planned)
        if new > max(0, 3 - previous):
            raise ValueError("planned knives exceed available member slots")
        historical_bosses = {k.boss for k in history if k.member == member.member}
        own = [k for k in planned if k.member == member.member]
        for boss in range(1, 6):
            matching = [k for k in own if k.boss == boss]
            if boss != 5:
                if len(matching) > 1 or matching and boss in historical_bosses:
                    raise ValueError("same member cannot repeat finite boss")
            else:
                finite = [k for k in matching if not (k.is_locked and k.source_locked_stage == 4)]
                if len(finite) > (0 if boss in historical_bosses else 1):
                    raise ValueError("same member cannot repeat finite boss")
    effective, assigned, tails = {}, {}, []
    for knife in history:
        damage = Decimal(knife.damage_true)
        if knife.stage <= 3:
            available = remaining[knife.stage - 1][knife.boss - 1]
            effective[knife.knife_id] = min(damage, available)
            remaining[knife.stage - 1][knife.boss - 1] = max(Decimal(0), available - damage)
        else:
            effective[knife.knife_id] = damage
    for boss in range(1, 6):
        check_execution()
        ordered = sorted((k for k in planned if k.boss == boss), key=lambda k: (
            not k.is_locked, k.source_locked_stage if k.is_locked else 0, member_map[k.member].sync,
            member_map[k.member].display_name, k.round, k.sequence, k.member.key, k.knife_id))
        for knife in ordered:
            damage = Decimal(knife.damage_true)
            stage = knife.source_locked_stage if knife.is_locked else next((i + 1 for i in range(3) if remaining[i][boss - 1] > 0), 0)
            if stage == 4 or stage == 0 and boss == 5:
                tails.append(knife)
                continue
            if stage:
                available = remaining[stage - 1][boss - 1]
                effective[knife.knife_id] = min(damage, available)
                remaining[stage - 1][boss - 1] = max(Decimal(0), available - damage)
            else:
                effective[knife.knife_id] = Decimal(0)
            assigned[knife.knife_id] = replace(knife, stage=stage)
    is_cleared = all(value == 0 for row in remaining for value in row)
    for knife in tails:
        effective[knife.knife_id] = Decimal(knife.damage_true) if is_cleared else Decimal(0)
        assigned[knife.knife_id] = replace(knife, stage=4 if is_cleared else 0)
    overflow = sum((Decimal(k.damage_true) - effective[k.knife_id] for k in planned if assigned[k.knife_id].stage == 3), Decimal(0))
    return RaidSimulation(tuple(assigned[k.knife_id] for k in planned),
        tuple((k.knife_id, decimal_text(effective[k.knife_id])) for k in all_knives),
        tuple(tuple(decimal_text(v) for v in row) for row in remaining),
        tuple(boss for boss in range(1, 6) if any(row[boss - 1] > 0 for row in remaining)), decimal_text(overflow))


def _lines(value) -> tuple[str, ...]:
    return tuple(line.strip() for line in re.split(r"\r\n|\r|\n", str(value)) if line.strip()) if value is not None else ()


def _nonnegative(value) -> Decimal:
    if type(value) not in (str, int, Decimal):
        raise ValueError("exact decimal string or integer required")
    text = (format(value, "f") if isinstance(value, Decimal) else str(value)).replace(",", "").strip()
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,6})?", text):
        raise ValueError("invalid exact damage")
    try:
        parsed = Decimal(text)
    except InvalidOperation as error:
        raise ValueError("invalid damage") from error
    if parsed > Decimal("1e24"):
        raise ValueError("damage exceeds local schema bound")
    return parsed


def _positive(value) -> Decimal:
    parsed = _nonnegative(value)
    if parsed <= 0:
        raise ValueError("positive damage required")
    return parsed


def _round_positive(value: float) -> int:
    if not math.isfinite(value) or value < 0 or value > 2 ** 53:
        raise ValueError("invalid binary64 estimate")
    lower = math.floor(value)
    return lower + (value - lower >= 0.5)
