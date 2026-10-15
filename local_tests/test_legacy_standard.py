from copy import deepcopy

import pytest

from adapters.legacy_standard import HEADERS, LegacyCatalog, read_legacy_standard
from adapters.sheet_standard import StandardReference
from adapters.local_state import LocalStateStore
from adapters.mock_sheets import MockSheets
from application.healthcheck import calculate_healthcheck
from application.publication import publish_workbooks
from application.sheet_healthcheck import reconcile_legacy_standard, run_healthcheck_job
from application.tasks import TaskSpec
from domain.snapshots import MemberKey, TrainingRecord, capture_document, capture_training, read_config


def test_six_rows_and_twelve_columns_decode_threshold_coefficient_and_weight():
    book, catalog = _fixture()
    config = _read(book, catalog)
    rules, labels = read_config(config)
    assert config.payload["model_version"] == 2
    assert rules[0].name_code == "role_demo"
    assert rules[0].total_weight == 2
    assert next(m for m in rules[0].metrics if m.metric_id == "skill_1").threshold == 5
    assert next(m for m in rules[0].metrics if m.metric_id == "trait_1").function_id == "attack_demo"
    assert labels[0].display_name == "mock_nickname"


def test_same_role_can_score_output_and_support_from_one_training_record():
    book, catalog = _fixture()
    book["inputs"]["legacy_checklist"].append(["mock_role", "電擊", "輔助", "攻擊力", "", "", "追蹤", "mock_nickname", ""])
    config = _read(book, catalog)
    rules, _ = read_config(config)
    assert len(rules) == 1 and set(rules[0].tracked_types) == {"output", "support"}
    member = MemberKey("area", "member")
    training = capture_training("union_demo", "2026-10-15T00:00:00Z", catalog.reference.version,
        (TrainingRecord(member, "role_demo", "owned", {"skill_1": 7}, {"attack_demo": 10}),))
    result = calculate_healthcheck(config, training, (member,), {member.key: "mock_member"}, "code_demo")
    assert len(result.scores.roles) == 1
    assert {row.key for row in result.scores.groups} == {"電擊", "輔助"}
    assert len(result.reports[0].assessments) == 1


def test_tracking_is_independent_per_role_type():
    book, catalog = _fixture()
    book["inputs"]["legacy_checklist"][1][6] = ""
    book["inputs"]["legacy_checklist"].append(["mock_role", "電擊", "輔助", "攻擊力", "", "", "追蹤", "mock_nickname", ""])
    rules, _ = read_config(_read(book, catalog))
    assert rules[0].tracked_types == ("support",)


def test_blank_rows_do_not_truncate_and_protected_identity_allows_rename():
    book, catalog = _fixture()
    book["inputs"]["legacy_checklist"].insert(1, [""] * 9)
    book["inputs"]["legacy_checklist"][0].append("角色ID")
    book["inputs"]["legacy_checklist"][2][0] = "renamed_mock_role"
    book["inputs"]["legacy_checklist"][2].append("role_demo")
    book["inputs"]["legacy_standard"][1][1] = "renamed_mock_role"
    assert read_config(_read(book, catalog))[0][0].name_code == "role_demo"


def test_legacy_eight_column_checklist_has_explicit_untracked_default():
    book, catalog = _fixture()
    for row in book["inputs"]["legacy_checklist"]:
        row.pop(6)
    rules, labels = read_config(_read(book, catalog))
    assert not rules[0].is_score_tracked and rules[0].tracked_types == ()
    assert labels[0].display_name == "mock_nickname"


@pytest.mark.parametrize("change", ["bad_width", "bad_group", "unknown_role", "unknown_function", "missing_role", "duplicate_type", "unbound_zero"])
def test_ambiguous_or_misaligned_legacy_input_rejected(change):
    book, catalog = _fixture()
    if change == "bad_width":
        book["inputs"]["legacy_standard"][1][2] = "unexpected_header"
    elif change == "bad_group":
        book["inputs"]["legacy_standard"][2][2] = "收藏品"
    elif change == "unknown_role":
        book["inputs"]["legacy_checklist"][1][0] = "unknown"
    elif change == "unknown_function":
        book["inputs"]["legacy_checklist"][1][3] = "unknown"
    elif change == "missing_role":
        book["inputs"]["legacy_standard"][1][1] = ""
    elif change == "duplicate_type":
        book["inputs"]["legacy_checklist"].append(deepcopy(book["inputs"]["legacy_checklist"][1]))
    else:
        book["inputs"]["legacy_standard"][3][11] = 0  # unselected trait_2 still requires an identity
    with pytest.raises(ValueError):
        _read(book, catalog)


def test_dual_element_copies_must_share_one_standard():
    book, catalog = _fixture()
    catalog = LegacyCatalog(catalog.reference, catalog.role_aliases, catalog.function_aliases, (("role_demo", ("燃燒", "鐵甲")),))
    book["inputs"]["legacy_checklist"][1][1] = "燃燒"
    grid = book["inputs"]["legacy_standard"]
    for row in grid:
        row.extend(deepcopy(row[1:13]))
    grid[0][1], grid[0][13] = "燃燒", "鐵甲"
    rules, _ = read_config(_read(book, catalog))
    assert rules[0].elements == ("燃燒", "鐵甲")
    grid[3][14] = 9
    with pytest.raises(ValueError, match="copies disagree"):
        _read(book, catalog)


def test_legacy_model_v1_remains_readable_but_extensions_require_v2():
    book, catalog = _fixture()
    config = _read(book, catalog)
    payload = config.payload
    del payload["model_version"]
    for row in payload["rules"]:
        del row["additional_role_types"]
        del row["tracked_role_types"]
    old = capture_document("healthcheck_config", "common", "2026-10-15T00:00:00Z", catalog.reference.version, payload)
    assert read_config(old)[0][0].tracked_types == ("output",)
    payload["rules"][0]["additional_role_types"] = ["support"]
    malformed = capture_document("healthcheck_config", "common", "2026-10-15T00:00:00Z", catalog.reference.version, payload)
    with pytest.raises(ValueError, match="version 2"):
        read_config(malformed)


def test_same_role_types_cannot_disagree_about_shared_priority_or_traits():
    book, catalog = _fixture()
    support = deepcopy(book["inputs"]["legacy_checklist"][1])
    support[2], support[8] = "輔助", "different_priority"
    book["inputs"]["legacy_checklist"].append(support)
    with pytest.raises(ValueError, match="shared standard"):
        _read(book, catalog)


def test_physical_short_template_is_not_silently_padded():
    book, catalog = _fixture()
    book["inputs"]["standard_column_count"] = 12
    with pytest.raises(ValueError, match="physical columns"):
        _read(book, catalog)


def test_horizontal_source_uses_same_frozen_config_for_calculation_and_mock_upload(tmp_path):
    book, catalog = _fixture()
    sheets = MockSheets(tmp_path / "books")
    sheets.create("legacy_demo", "legacy_v1", book["inputs"])
    sheets.create("output_demo", "output_v1", {"manual_note": "keep"})
    store = LocalStateStore(tmp_path / "state.sqlite")
    try:
        candidate = reconcile_legacy_standard(store, sheets, "legacy_demo", "common", "2026-10-15T00:00:00Z", catalog)
        book["inputs"]["legacy_standard"][3][2] = 9
        sheets.edit_inputs("legacy_demo", book["inputs"])
        member = MemberKey("area", "member")
        training = capture_training("union_demo", "2026-10-15T00:00:00Z", catalog.reference.version,
            (TrainingRecord(member, "role_demo", "owned", {"skill_1": 7}, {"attack_demo": 10}),))
        inputs = capture_document("union_inputs", "union_demo", "2026-10-15T00:00:00Z", catalog.reference.version,
            {"members": [{"member": {"area_id": "area", "member_id": "member"}, "display_name": "mock_member"}],
             "healthcheck_route": {"resource": "output_demo", "layout_revision": "output_v1"}})
        for document in (training, inputs):
            store.put_document(document)
        target = TaskSpec("recompute_healthcheck", "union_demo", "mock_code", {"training": training.snapshot_id, "union_inputs": inputs.snapshot_id})
        store.publish_candidate(candidate, (target,))
        lease = store.claim("mock_worker", "mock_code")
        job = run_healthcheck_job(store, lease)
        result = store.get_result(job.result_id)
        claim = store.begin_publication(job.job_id, "mock_publisher", "mock_code", ("output_demo",))
        publish_workbooks(store, sheets, claim, result["workbooks"], result["layout_revisions"])
        generated = sheets.read("output_demo")["generated"]
        assert generated["standards"]["rules"][0]["metrics"][1]["threshold"] == 5
        assert generated["config_id"] == store.candidate_document(candidate).snapshot_id
        assert sheets.read("output_demo")["inputs"] == {"manual_note": "keep"}
    finally:
        store.close()


def test_ambiguous_names_require_protected_id_and_contradictory_ids_are_rejected():
    book, catalog = _fixture()
    reference = StandardReference("reference_demo", ("role_demo", "role_other"), ("attack_demo",))
    ambiguous = LegacyCatalog(reference, (("mock_role", "role_demo"), ("mock_role", "role_other")), catalog.function_aliases)
    with pytest.raises(ValueError, match="ambiguous"):
        _read(book, ambiguous)
    checklist = book["inputs"]["legacy_checklist"]
    checklist[0].append("角色ID")
    checklist[1].append("role_demo")
    assert read_config(_read(book, ambiguous))[0][0].name_code == "role_demo"
    contradictory = LegacyCatalog(reference, (("mock_role", "role_demo"),), catalog.function_aliases)
    checklist[1][-1] = "role_other"
    with pytest.raises(ValueError, match="contradicts"):
        _read(book, contradictory)


def _read(book, catalog):
    return read_legacy_standard(book, "common", "2026-10-15T00:00:00Z", catalog)


def _fixture():
    reference = StandardReference("reference_demo", ("role_demo",), ("attack_demo",))
    catalog = LegacyCatalog(reference, (("mock_role", "role_demo"),), (("攻擊力", "attack_demo"),))
    grid = [[""] * 13 for _ in range(6)]
    grid[0][1] = "電擊"
    grid[1][1] = "mock_role"
    for offset, label in ((0, "突破"), (1, "技能"), (4, "裝備 (頭/手/身/腳)"), (8, "收藏品"), (9, "詞條 (攻)")):
        grid[2][1 + offset] = label
    grid[3][2], grid[4][2], grid[5][1] = 5, 1, 2
    return {"inputs": {"legacy_checklist": [list(HEADERS), ["mock_role", "電擊", "輸出", "攻擊力", "", "", "追蹤", "mock_nickname", ""]],
                        "legacy_standard": grid}}, catalog
