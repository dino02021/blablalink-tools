import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import subprocess

from domain.raid import RaidKnife, RaidMember, estimate_damage_yi, simulate_stages
from domain.snapshots import MemberKey

ROOT = Path(__file__).resolve().parent


def test_frozen_gs_damage_and_stage_normalization_parity(tmp_path):
    source = ROOT / "fixtures/gs_raid_reference.js"
    metadata = json.loads((ROOT / "fixtures/gs_raid_provenance.json").read_text(encoding="utf-8"))
    assert hashlib.sha256(source.read_text(encoding="utf-8").encode()).hexdigest() == metadata["fixture_sha256"]
    random_source = random.Random(42)
    estimates = [[random_source.randint(100, 1000), random_source.randint(100, 1000), random_source.randint(1, 10000)] for _ in range(100)]
    members = (RaidMember(MemberKey("area", "a"), "a", 100), RaidMember(MemberKey("area", "b"), "b", 200))
    cases, expected = [], []
    for _ in range(40):
        hp = tuple(tuple(random_source.randint(0, 2) * 100 for _ in range(5)) for _ in range(3))
        knives = tuple(RaidKnife(f"{index}:{boss}", member.member, boss, str(random_source.randint(1, 5) * 100))
                       for index, member in enumerate(members) for boss in random_source.sample(range(1, 6), 3))
        simulation = simulate_stages(members, hp, (), knives)
        effective = dict(simulation.effective_true)
        expected.append({k.knife_id: (k.stage, float(effective[k.knife_id])) for k in simulation.knives})
        cases.append({"hp": hp, "members": [{"key": m.member.key, "name": m.display_name, "sync": m.sync} for m in members],
            "knives": [{"id": k.knife_id, "member": k.member.key, "bossIndex": k.boss - 1, "damageTrue": int(k.damage_true),
                        "stage": k.stage, "lockedStage": k.is_locked, "sequence": k.sequence, "round": k.round} for k in knives]})
    environment = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP") if key in os.environ}
    node = shutil.which("node")
    assert node, "Node required for offline GS parity"
    completed = subprocess.run([node, str(ROOT / "gs_raid_runner.js")], input=json.dumps({"estimates": estimates, "simulations": cases}),
        capture_output=True, text=True, check=True, timeout=5, cwd=tmp_path, env=environment)
    actual = json.loads(completed.stdout)
    assert actual["isolated"]
    assert actual["estimates"] == [estimate_damage_yi(*args) for args in estimates]
    assert [{k["id"]: (k["stage"], k["effective"]) for k in case} for case in actual["simulations"]] == expected
