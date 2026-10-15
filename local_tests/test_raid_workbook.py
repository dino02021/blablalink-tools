from contextlib import closing
from dataclasses import asdict, replace

import pytest

from adapters.local_raid_plans import LocalRaidPlans
from application.raid_workbook import render_raid_workbook, restore_raid_workbook
from domain.healthcheck import ELEMENTS
from domain.raid import FixedInputs, RaidKnife, RaidMember
from domain.raid_finalize import finalize_rounds
from domain.raid_planner import RaidProblem, select_rounds
from domain.snapshots import MemberKey, canonical_json


@pytest.fixture
def problem():
    members = (RaidMember(MemberKey("area", "first"), "=literal", 100), RaidMember(MemberKey("area", "second"), "=literal", 200))
    return RaidProblem(members, (("0",)*5,)*3, ELEMENTS, 100, ("10",)*5, (),
        ((members[0].member, FixedInputs(("正五", "正五", "正五"))),),
        tuple((member.member, tuple((element, index+1) for index, element in enumerate(ELEMENTS))) for member in members),
        (("inputs", "mock"),), "code")


def render(problem):
    final = finalize_rounds(problem, select_rounds(problem))
    return final, render_raid_workbook(problem, final)


def test_five_tabs_pin_exact_inputs_and_reconstruct_without_network(problem):
    final, workbook = render(problem)
    assert workbook.problem_id == problem.problem_id and workbook.draft_plan_id == final.draft_plan_id
    assert workbook.code_sha == problem.code_sha and len(workbook.tabs) == 5
    assert restore_raid_workbook(asdict(workbook)) == workbook
    assert all(tab.is_generated_protected for tab in workbook.tabs)
    overview = workbook.tabs[0]
    assert overview.frozen_rows == 2 and overview.hidden_columns == (0,)
    assert (0, 1, 12, 14) in overview.merges
    assert overview.typed_rows()[2][1]["value"] == {"stringValue": "=literal"}


def test_sorted_overview_does_not_reorder_or_rewrite_fixed_inputs(problem):
    first = problem.members[0].member
    history = tuple(RaidKnife(f"history_{i}", first, 5, "100000000", "history", 4, True, sequence=i) for i in range(3))
    problem = replace(problem, history=history)
    original = canonical_json(asdict(problem))
    _, workbook = render(problem)
    overview, _, mirror, _, _ = workbook.tabs
    assert overview.row_ids[2:] == (problem.members[1].member.key, first.key)
    assert mirror.row_ids[1:] == tuple(member.member.key for member in problem.members)
    assert tuple(cell.value for cell in mirror.rows[1][2:5]) == ("正五",)*3
    assert canonical_json(asdict(problem)) == original


def test_history_gray_and_mixed_lines_keep_per_shot_origin(problem):
    first = problem.members[0].member
    history = (RaidKnife("history", first, 5, "900719925474099300000000", "history", 4, True),)
    _, workbook = render(replace(problem, history=history))
    overview, shots, *_ = workbook.tabs
    first_row = overview.rows[overview.row_ids.index(first.key)]
    assert first_row[12].note == "含歷史與新出刀，逐刀來源見出刀明細"
    assert len(first_row[12].value.splitlines()) == 3
    history_row = next(row for row in shots.typed_rows()[1:] if row[2]["value"] == {"stringValue": "history"})
    assert history_row[4]["value"] == {"stringValue": "900719925474099300000000"}
    assert history_row[4]["foreground_hex"] == "#808080"
    assert history_row[7]["value"] == {"boolValue": True}
    assert any(row[7]["value"] == {"boolValue": False} and row[4]["foreground_hex"] == "#000000" for row in shots.typed_rows()[1:])


def test_warning_and_boss_deficits_are_visible_and_bound_to_identity(problem):
    problem = replace(problem, hp=(("1000000000000",)*5,)*3)
    final, workbook = render(problem)
    warnings, bosses = workbook.tabs[3:]
    assert len(warnings.rows)-1 == len(final.warnings)
    assert all(row[2].style == "unknown" for row in warnings.rows[1:])
    for boss in final.simulation.deficit_bosses:
        row = bosses.rows[bosses.row_ids.index(f"3:{boss}")]
        assert row[2].style == "failed" and row[3].value is True
    with pytest.raises(ValueError, match="warnings"):
        render_raid_workbook(problem, replace(final, warnings=()))


def test_stale_confirmation_keeps_its_original_workbook(problem, tmp_path):
    final, before = render(problem)
    with closing(LocalRaidPlans(tmp_path / "state.sqlite")) as store:
        revision = store.observe_problem("union", problem, 0)
        draft = store.save_draft("union", problem, final)
        confirmation = store.confirm("request", "intent", "union", draft, "actor", revision, None)
        shown = store.confirmation_workbook(confirmation)
        assert shown["workbook_view_id"] == before.view_id and not shown["is_stale"]
        changed = replace(problem, input_versions=(("inputs", "new"),))
        store.observe_problem("union", changed, revision)
        updated = store.confirmation_workbook(confirmation)
        assert updated["is_stale"] and updated["workbook_view_id"] == before.view_id
        assert updated["workbook_view"] == shown["workbook_view"]


def test_wrong_final_and_foreign_warnings_cannot_render(problem):
    final, _ = render(problem)
    with pytest.raises(ValueError, match="mismatch"):
        render_raid_workbook(replace(problem, input_versions=(("inputs", "other"),)), final)
    with pytest.raises(ValueError, match="roster"):
        render_raid_workbook(problem, replace(final, warnings=((*final.warnings, (MemberKey("other", "foreign").key, "warning")))))
