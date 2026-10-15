import pytest

from adapters.legacy_standard import HEADERS, LegacyCatalog, read_legacy_standard
from adapters.sheet_standard import StandardReference
from application.healthcheck import calculate_healthcheck
from application.workbook_views import render_healthcheck_view
from domain.healthcheck import MetricRule, RoleRule
from domain.snapshots import MemberKey, Override, RoleLabel, TrainingRecord, capture_config, capture_training, read_config, read_training
from domain.view_grid import ViewCell, ViewTab
from domain.view_grid import restore_workbook_view
from adapters.mock_sheets import MockSheets
from executors.local.demo import run_demo


@pytest.fixture
def rendered():
    members = (MemberKey("area", "member_a"), MemberKey("area", "member_b"))
    rule = RoleRule("role_mock", ("電擊", "燃燒"), "output",
        (MetricRule("skill_1", 5, 1), MetricRule("trait_1", 10, 1, "attack_mock")), 2)
    labels = (RoleLabel("role_mock", "mock_name", trait_labels=(("attack_mock", "攻擊力"),)),)
    config = capture_config("common", "2026-10-15T00:00:00Z", "reference", (rule,), labels)
    training = capture_training("union", "2026-10-15T00:00:00Z", "reference", (
        TrainingRecord(members[0], "role_mock", "owned", {"skill_1": 3}, {"attack_mock": 12}),
        TrainingRecord(members[1], "role_mock", "not_owned")))
    names = {member.key: "=IMPORTDATA(\"never-evaluate\")" for member in members}
    result = calculate_healthcheck(config, training, members, names, "code")
    view = render_healthcheck_view(config, training, members, names, result)
    return config, training, members, names, result, view


def test_all_tabs_bind_one_source_and_keep_composite_identity(rendered):
    config, training, members, _, result, view = rendered
    assert len(view.tabs) == 8
    assert (view.config_id, view.training_id, view.calculation_id) == (config.snapshot_id, training.snapshot_id, result.result_id)
    for tab in view.tabs:
        assert tab.is_generated_protected
        assert all(len(row) == len(tab.column_ids) for row in tab.rows)
        if tab.tab_key != "standards":
            assert tab.row_ids[-2:] == tuple(member.key for member in members)
            assert tab.hidden_columns == (0,)


def test_training_statuses_colors_and_formula_like_names_are_literal(rendered):
    _, _, _, _, _, view = rendered
    tab = next(tab for tab in view.tabs if tab.tab_key == "training_電擊")
    typed = tab.typed_rows()
    assert typed[2][1]["value"] == {"stringValue": '=IMPORTDATA("never-evaluate")'}
    assert typed[2][3]["style"] == "failed" and typed[2][3]["foreground_hex"] == "#D93025"
    assert typed[2][11]["style"] == "passed"
    assert typed[3][3]["value"] == {} and typed[3][3]["style"] == "not_owned"


def test_standard_grid_round_trips_selected_pinned_rules(rendered):
    config, _, _, _, _, view = rendered
    standard = view.tabs[0]
    reference = StandardReference("reference", ("role_mock",), ("attack_mock",))
    catalog = LegacyCatalog(reference, (("mock_name", "role_mock"),), (("攻擊力", "attack_mock"),),
        (("role_mock", ("電擊", "燃燒")),))
    rows = [[cell.value for cell in row] for row in standard.rows]
    book = {"inputs": {"legacy_standard": rows, "standard_column_count": len(standard.column_ids),
        "legacy_checklist": [list(HEADERS), ["mock_name", "電擊", "輸出", "攻擊力", "", "", "追蹤", "mock_name", ""]]}}
    restored = read_legacy_standard(book, "common", "2026-10-15T00:00:00Z", catalog)
    before, _ = read_config(config)
    after, _ = read_config(restored)
    # The physical template makes the other nine blank metric slots explicit.
    selected = {metric.metric_id: metric for metric in after[0].metrics}
    assert all(selected[metric.metric_id] == metric for metric in before[0].metrics)
    assert after[0].elements == before[0].elements and after[0].total_weight == before[0].total_weight


def test_changed_names_or_overrides_cannot_replace_view_inputs(rendered):
    config, training, members, names, result, _ = rendered
    with pytest.raises(ValueError):
        render_healthcheck_view(config, training, members, {**names, members[0].key: "changed"}, result)
    with pytest.raises(ValueError):
        render_healthcheck_view(config, training, members, names, result, (Override(members[0], "role_mock", "metric", "skill_1", value=10),))


def test_view_applies_same_override_without_rewriting_training(rendered):
    config, training, members, names, _, _ = rendered
    overrides = (Override(members[0], "role_mock", "metric", "skill_1", value=6),)
    result = calculate_healthcheck(config, training, members, names, "code", overrides)
    view = render_healthcheck_view(config, training, members, names, result, overrides)
    tab = next(tab for tab in view.tabs if tab.tab_key == "training_電擊")
    assert tab.rows[2][3].value == 6 and tab.rows[2][3].style == "passed"
    assert dict(read_training(training)[0].metrics)["skill_1"] == 3


def test_layout_revision_is_independent_of_training_values(rendered):
    config, training, members, names, result, before = rendered
    records = (TrainingRecord(members[0], "role_mock", "owned", {"skill_1": 10}, {"attack_mock": 12}),
        TrainingRecord(members[1], "role_mock", "not_owned"))
    changed = capture_training("union", "2026-10-15T00:01:00Z", "reference", records)
    recalculated = calculate_healthcheck(config, changed, members, names, "code")
    after = render_healthcheck_view(config, changed, members, names, recalculated)
    assert before.view_id != after.view_id
    assert [tab.layout_revision for tab in before.tabs] == [tab.layout_revision for tab in after.tabs]


def test_grid_rejects_hidden_data_merges_and_unprotected_generated_cells():
    with pytest.raises(ValueError, match="hides"):
        ViewTab("tab", "name", ("a", "b"), ("row",), ((ViewCell("first"), ViewCell("second")),), ((0, 1, 0, 2),))
    with pytest.raises(ValueError):
        ViewTab("tab", "name", ("a",), ("row",), ((ViewCell(),),), is_generated_protected=False)


def test_mock_publication_includes_reconstructable_same_version_view(tmp_path):
    summary = run_demo(tmp_path / "demo")
    generated = MockSheets(tmp_path / "demo/workbooks").read("output_demo")["generated"]
    view = restore_workbook_view(generated["view"])
    assert view.view_id == generated["view_id"]
    assert view.config_id == summary["published_config_id"]
    assert view.training_id == summary["training_snapshot_id"]
    assert view.calculation_id == generated["calculation_id"]
    assert len(view.tabs) == 8
