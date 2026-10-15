from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from adapters.local_raid_plans import LocalRaidPlans
from application.tasks import StateConflict
from domain.healthcheck import ELEMENTS
from domain.raid import FixedInputs, RaidKnife, RaidMember, simulate_stages
from domain.raid_planner import RaidProblem, select_rounds
from domain.raid_finalize import finalize_rounds
from domain.raid_view import build_raid_view
from domain.snapshots import MemberKey


def test_sorted_generated_view_keeps_identity_original_inputs_and_multiline_tail():
    problem = _problem()
    final = finalize_rounds(problem, select_rounds(problem))
    view = build_raid_view(problem, final)
    assert [r.member for r in view.rows] == [m.member for m in problem.members]
    assert [r.new_knife_count for r in view.rows] == [3, 3]
    for row in view.rows:
        tail = row.bosses[4]
        assert len(tail.damage_text.splitlines()) == 3
        assert tail.stage_text == "IV"
        assert not tail.is_all_history
        assert all(not shot.is_history for shot in tail.shots)
    assert dict(problem.fixed_inputs)[problem.members[0].member].slots == ("正五", "正五", "正五")


def test_history_only_member_sorts_after_new_knives_and_is_gray_by_source():
    problem = _problem()
    first, second = [m.member for m in problem.members]
    history = tuple(RaidKnife(f"history_{i}", first, 5, "100000000", "history", 4, True, sequence=i) for i in range(3))
    problem = replace(problem, history=history, fixed_inputs=())
    final = finalize_rounds(problem, select_rounds(problem))
    view = build_raid_view(problem, final)
    assert [row.member for row in view.rows] == [second, first]
    assert view.rows[1].new_knife_count == 0
    assert view.rows[1].bosses[4].is_all_history
    assert len(view.rows[1].bosses[4].shots) == 3


def test_confirmed_plan_survives_new_inputs_without_silent_replacement(tmp_path):
    store = LocalRaidPlans(tmp_path / "state.sqlite")
    problem = _problem()
    final = finalize_rounds(problem, select_rounds(problem))
    try:
        revision = store.observe_problem("union_demo", problem, 0)
        draft = store.save_draft("union_demo", problem, final)
        confirmed = store.confirm("request", "intent", "union_demo", draft, "actor_demo", revision, None)
        original = store.confirmation(confirmed)
        assert not store.confirmation_status(confirmed)["is_stale"]
        changed = replace(problem, input_versions=(("ranks", "new_version"),))
        store.observe_problem("union_demo", changed, revision)
        store.save_draft("union_demo", changed, finalize_rounds(changed, select_rounds(changed)))
        assert store.confirmed_head("union_demo") == confirmed
        assert store.confirmation(confirmed) == original
        assert store.confirmation_status(confirmed)["is_stale"]
        assert store.confirm("request", "intent", "union_demo", draft, "actor_demo", revision, None) == confirmed
        with pytest.raises(StateConflict, match="stale"):
            store.confirm("new_request", "new_intent", "union_demo", draft, "actor_demo", revision, confirmed)
    finally:
        store.close()


def test_confirmation_requires_explicit_warning_acceptance_and_exact_base(tmp_path):
    store = LocalRaidPlans(tmp_path / "state.sqlite")
    problem = replace(_problem(), hp=(("1000000000000",) * 5,) * 3)
    try:
        final = finalize_rounds(problem, select_rounds(problem))
        # Repeated fixed IV-5 cannot land before front clear.
        assert final.warnings
        store.observe_problem("union_demo", problem, 0)
        draft = store.save_draft("union_demo", problem, final)
        with pytest.raises(StateConflict, match="warnings"):
            store.confirm("request", "intent", "union_demo", draft, "actor", 1, None)
        confirmation = store.confirm("request", "intent", "union_demo", draft, "actor", 1, None, True)
        with pytest.raises(StateConflict):
            store.confirm("different", "other_intent", "union_demo", draft, "actor", 1, None, True)
        with pytest.raises(StateConflict):
            store.confirm("request", "intent", "union_demo", draft, "different_actor", 1, None, True)
        assert store.confirmed_head("union_demo") == confirmation
    finally:
        store.close()


def test_restart_and_two_connections_cannot_confirm_over_same_base(tmp_path):
    path = tmp_path / "state.sqlite"
    store = LocalRaidPlans(path)
    problem = _problem()
    store.observe_problem("union_demo", problem, 0)
    draft = store.save_draft("union_demo", problem, finalize_rounds(problem, select_rounds(problem)))
    store.close()
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(_confirm, (path,) * 2, (draft,) * 2, (barrier,) * 2, ("one", "two")))
    assert outcomes.count("conflict") == 1
    store = LocalRaidPlans(path)
    try:
        confirmation = store.confirmed_head("union_demo")
        assert confirmation in outcomes
        assert store.confirmation(confirmation)["view"]["draft_plan_id"] == draft
        with pytest.raises(StateConflict):
            store.draft("other_union", draft)
    finally:
        store.close()


def test_forged_draft_cannot_change_automatic_damage():
    problem = replace(_problem(), fixed_inputs=())
    final = finalize_rounds(problem, select_rounds(problem))
    wrong_knife = replace(final.simulation.knives[0], damage_true="1")
    wrong = replace(final, simulation=simulate_stages(problem.members, problem.hp, (), (wrong_knife, *final.simulation.knives[1:])))
    with pytest.raises(ValueError, match="estimate"):
        build_raid_view(problem, wrong)


def test_shortage_warning_cannot_be_removed_before_confirmation():
    problem = replace(_problem(), hp=(("1000000000000",) * 5,) * 3)
    final = finalize_rounds(problem, select_rounds(problem))
    assert final.warnings
    with pytest.raises(ValueError, match="warnings"):
        build_raid_view(problem, replace(final, warnings=()))


def _confirm(path, draft, barrier, intent):
    store = LocalRaidPlans(path)
    try:
        barrier.wait()
        try:
            return store.confirm(intent, intent, "union_demo", draft, "actor", 1, None)
        except StateConflict:
            return "conflict"
    finally:
        store.close()


def _problem():
    members = (RaidMember(MemberKey("area", "first"), "same", 100), RaidMember(MemberKey("area", "second"), "same", 200))
    return RaidProblem(members, (("0",) * 5,) * 3, ELEMENTS, 100, ("10",) * 5, (),
        ((members[0].member, FixedInputs(("正五", "正五", "正五"))),),
        tuple((m.member, tuple((e, i + 1) for i, e in enumerate(ELEMENTS))) for m in members), (("inputs", "demo"),), "code_demo")
