import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import subprocess

from domain.raid import RaidMember
from domain.raid_planner import RaidProblem, build_source_pool
from domain.healthcheck import ELEMENTS
from domain.snapshots import MemberKey


def test_source_pool_matches_frozen_gs_with_unique_ranks(tmp_path):
    root = Path(__file__).resolve().parent
    metadata = json.loads((root / "fixtures/gs_raid_pool_provenance.json").read_text(encoding="utf-8"))
    fixture = (root / "fixtures/gs_raid_pool_reference.js").read_text(encoding="utf-8")
    assert hashlib.sha256(fixture.encode()).hexdigest() == metadata["fixture_sha256"]
    generator = random.Random(123)
    payload, expected = [], []
    for case in range(25):
        member = RaidMember(MemberKey("area", "member"), "member", 100 + case)
        order = generator.sample(list(ELEMENTS), 5)
        ranks = tuple((e, i + 1) for i, e in enumerate(order))
        damages = tuple(str(generator.randint(1, 1000)) for _ in range(5))
        problem = RaidProblem((member,), (("1000000000000",) * 5,) * 3, ELEMENTS, 100, damages,
            (), (), ((member.member, ranks),), (("source", "demo"),), "code_demo")
        pool = build_source_pool(problem)
        expected.append([{"member": t.knife.member.key, "boss": t.knife.boss, "damage": t.knife.damage_true,
                          "group": t.group_rank, "top": t.top_order, "kind": t.knife.kind} for t in pool])
        payload.append({"baseSync": 100, "baseDamageYi": [int(d) for d in damages], "bossElements": ELEMENTS,
            "members": [{"key": member.member.key, "name": "member", "sync": member.sync, "ranks": ranks,
                         "reverseInputs": [], "fixedKnives": [], "historyKnives": []}]})
    node = shutil.which("node")
    assert node
    environment = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP") if key in os.environ}
    output = subprocess.run([node, str(root / "gs_raid_pool_runner.js")], input=json.dumps(payload),
        capture_output=True, text=True, check=True, timeout=5, cwd=tmp_path, env=environment)
    assert json.loads(output.stdout) == expected
