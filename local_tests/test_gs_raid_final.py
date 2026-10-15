import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
from decimal import Decimal

from domain.healthcheck import ELEMENTS
from domain.raid import FixedInputs, RaidKnife, RaidMember, prepare_fixed
from domain.raid_planner import RaidProblem, select_rounds
from domain.raid_finalize import finalize_rounds
from domain.snapshots import MemberKey


def test_finalized_assignments_match_frozen_gs_for_mock_cases(tmp_path):
    root = Path(__file__).resolve().parent
    fixture = (root / "fixtures/gs_raid_final_reference.js").read_text(encoding="utf-8")
    metadata = json.loads((root / "fixtures/gs_raid_final_provenance.json").read_text(encoding="utf-8"))
    assert hashlib.sha256(fixture.encode()).hexdigest() == metadata["fixture_sha256"]
    generator = random.Random(18)
    payload, expected, physical_overflow = [], [], []
    for case in range(60):
        members = tuple(RaidMember(MemberKey("area", str(i)), f"member_{i}", 100 + i * 20) for i in range(3))
        ranks = tuple((m.member, tuple((e, i + 1) for i, e in enumerate(generator.sample(list(ELEMENTS), 5)))) for m in members)
        hp = tuple(tuple(str(generator.randint(0, 10) * 100000000) for _ in range(5)) for _ in range(3))
        if case % 5 == 0:
            hp = (("0",) * 5,) * 3
        slots = ("正五", "正五", "正五") if case % 6 == 0 else ("正一", "負二", "") if case % 3 == 0 else ("", "", "")
        fixed = ((members[0].member, FixedInputs(slots)),)
        history = (RaidKnife("history_demo", members[-1].member, 1, "100000000", "history", 1, True),) if case % 4 == 0 else ()
        problem = RaidProblem(members, hp, ELEMENTS, 100, ("3",) * 5, history, fixed, ranks, (("inputs", "demo"),), "code_demo")
        result = finalize_rounds(problem, select_rounds(problem))
        physical_overflow.append(Decimal(result.simulation.phase_three_overflow_true))
        expected.append(_sort([{"member": k.member.key, "boss": k.boss, "stage": k.stage, "kind": k.kind, "damage": k.damage_true}
                               for k in result.simulation.knives]))
        rows = []
        for member in members:
            history = tuple(k for k in problem.history if k.member == member.member)
            preparation = prepare_fixed(member, dict(problem.fixed_inputs).get(member.member, FixedInputs(("", "", ""))), history, 100, problem.base_damage_yi)
            rows.append({"key": member.member.key, "name": member.display_name, "sync": member.sync, "ranks": dict(ranks)[member.member],
                "historyKnives": [{"bossIndex": k.boss - 1, "stage": k.stage, "damageTrue": int(k.damage_true),
                                   "elementKey": ELEMENTS[k.boss - 1]} for k in history],
                "reverseInputs": [{"bossIndex": b - 1} for b in preparation.blocked_bosses],
                "fixedKnives": [{"bossIndex": k.boss - 1, "stage": k.stage, "lockedStage": k.is_locked,
                    "damageTrue": int(k.damage_true), "damageYi": int(k.damage_true) // 100000000, "elementKey": ELEMENTS[k.boss - 1],
                    "kind": "fixed", "slotIndex": k.slot, "assigned": False} for k in preparation.knives]})
        payload.append({"members": rows, "hp": [[int(v) for v in row] for row in hp], "bossElements": ELEMENTS,
                        "baseSync": 100, "baseDamageYi": [3] * 5})
    node = shutil.which("node")
    assert node
    environment = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP") if key in os.environ}
    completed = subprocess.run([node, str(root / "gs_raid_final_runner.js")], input=json.dumps(payload),
        text=True, capture_output=True, timeout=10, check=True, env=environment, cwd=tmp_path)
    responses = json.loads(completed.stdout)
    assert all(response["isolated"] for response in responses)
    assert all(response["legacyOverflow"] == 0 for response in responses)
    assert any(value > 0 for value in physical_overflow)
    actual = [_sort(response["assignments"]) for response in responses]
    for index, (found, wanted) in enumerate(zip(actual, expected)):
        assert found == wanted, f"GS/Python finalization case {index} differs"


def _sort(rows):
    return sorted(rows, key=lambda row: (row["member"], row["kind"], row["boss"], row["stage"], row["damage"]))
