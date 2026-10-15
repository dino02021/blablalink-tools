"""Only fabricated payloads; originals and credentials are never loaded."""

from dataclasses import FrozenInstanceError

import pytest

from domain.healthcheck import MetricRule, RoleRule
from domain.snapshots import (
    Document, MemberKey, Override, RoleLabel, TrainingRecord, apply_overrides,
    canonical_json, capture_config, capture_document, capture_training,
    diff_training, metric_values, read_config, read_training, restore_document,
)


WHEN = "2026-10-15T00:00:00Z"
MEMBER = MemberKey("area_demo", "member_demo")


def test_document_is_detached_content_addressed_and_round_trips():
    payload = {"roles": [{"value": 10}]}
    document = capture_document("raw", "union_demo", WHEN, "reference_demo", payload)
    original_id = document.snapshot_id
    payload["roles"][0]["value"] = 999
    document.payload["roles"][0]["value"] = 888
    assert document.payload == {"roles": [{"value": 10}]}
    assert document.snapshot_id == original_id
    assert restore_document(document.serialized, original_id) == document
    with pytest.raises(FrozenInstanceError):
        document.serialized = "{}"
    with pytest.raises(ValueError, match="hash mismatch"):
        restore_document(document.serialized.replace('"value":10', '"value":11'), original_id)


def test_canonical_order_and_timezone_but_separate_capture_identity():
    first = capture_document("raw", "union_demo", WHEN, "reference_demo", {"b": 2, "a": 1})
    second = capture_document("raw", "union_demo", "2026-10-15T08:00:00+08:00", "reference_demo", {"a": 1, "b": 2})
    later = capture_document("raw", "union_demo", "2026-10-16T00:00:00Z", "reference_demo", {"b": 2, "a": 1})
    assert first == second
    assert first.payload_hash == later.payload_hash
    assert first.snapshot_id != later.snapshot_id


@pytest.mark.parametrize("key", ["password", "Cookie", "authorization", "private-key", "sessions", "storage_state",
                               " accessToken ", "APIKey", "refreshToken", "session_token"])
def test_secret_keys_rejected_even_when_nested(key):
    with pytest.raises(ValueError, match="credentials"):
        capture_document("raw", "union_demo", WHEN, "reference_demo", {"nested": [{key: "fake_secret"}]})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), object(), {1: "bad_key"}])
def test_non_json_values_are_rejected(value):
    with pytest.raises(ValueError):
        canonical_json({"value": value})


def test_naive_timestamp_unsupported_schema_and_kind_are_rejected():
    with pytest.raises(ValueError, match="timezone"):
        capture_document("raw", "union_demo", "2026-10-15", "reference_demo", {})
    document = capture_document("raw", "union_demo", WHEN, "reference_demo", {})
    with pytest.raises(ValueError, match="schema"):
        Document(document.serialized.replace('"schema_version":1', '"schema_version":true'))
    with pytest.raises(ValueError, match="kind"):
        capture_document("arbitrary", "union_demo", WHEN, "reference_demo", {})
    with pytest.raises(ValueError, match="duplicate JSON"):
        Document(document.serialized.replace('"schema_version":1', '"schema_version":2,"schema_version":1'))


def test_member_key_is_region_scoped_and_delimiter_safe():
    assert MemberKey("a/b", "c").key != MemberKey("a", "b/c").key
    assert MemberKey("area_one", "same_id") != MemberKey("area_two", "same_id")


def test_config_roundtrip_freezes_rules_metadata_and_trait_identity():
    elements = ["燃燒", "鐵甲"]
    metrics = [MetricRule("trait_1", 10, 1, "attack_demo")]
    rule = RoleRule("nikke_demo", elements, "output", metrics, 1)
    document = capture_config("common", WHEN, "reference_demo", (rule,),
                              (RoleLabel("nikke_demo", "示範角色", "優先", {"attack_demo": "攻擊"}),))
    elements.clear()
    metrics.clear()
    rules, labels = read_config(document)
    assert rules == (rule,)
    assert rules[0].elements == ("燃燒", "鐵甲")
    assert labels[0].trait_labels == (("attack_demo", "攻擊"),)
    with pytest.raises(ValueError, match="unknown label"):
        capture_config("common", WHEN, "reference_demo", (rule,), (RoleLabel("other", "錯誤角色"),))


def test_training_roundtrip_order_and_mutation_isolation():
    values = {"skill_1": 10}
    record = TrainingRecord(MEMBER, "nikke_demo", "owned", values, {"attack_demo": 15})
    values["skill_1"] = 99
    document = capture_training("union_demo", WHEN, "reference_demo", (record,))
    assert read_training(document) == (record,)
    assert dict(record.metrics) == {"skill_1": 10}
    with pytest.raises(ValueError, match="duplicate training"):
        capture_training("union_demo", WHEN, "reference_demo", (record, record))
    with pytest.raises(ValueError, match="not_owned"):
        TrainingRecord(MEMBER, "nikke_demo", "not_owned", {"skill_1": 10})


@pytest.mark.parametrize("value", [True, [], float("nan"), 10 ** 1000])
def test_invalid_training_values_rejected(value):
    with pytest.raises(ValueError):
        TrainingRecord(MEMBER, "nikke_demo", "owned", {"skill_1": value})


def test_selected_traits_project_by_function_id_not_previous_slot():
    record = TrainingRecord(MEMBER, "nikke_demo", "owned", {}, {"attack_demo": 20, "ammo_demo": 50})
    original = RoleRule("nikke_demo", ("電擊",), "output",
                        (MetricRule("trait_1", 10, 1, "attack_demo"), MetricRule("trait_2", 40, 1, "ammo_demo")))
    swapped = RoleRule("nikke_demo", ("電擊",), "output",
                       (MetricRule("trait_1", 40, 1, "ammo_demo"), MetricRule("trait_2", 10, 1, "attack_demo")))
    assert metric_values(original, record) == {"trait_1": 20, "trait_2": 50}
    assert metric_values(swapped, record) == {"trait_1": 50, "trait_2": 20}


def test_override_zero_blank_disabled_clear_and_original_preserved():
    record = TrainingRecord(MEMBER, "nikke_demo", "owned", {"skill_1": 10})
    zero = Override(MEMBER, "nikke_demo", "metric", "skill_1", 0)
    blank = Override(MEMBER, "nikke_demo", "metric", "skill_1", None)
    disabled = Override(MEMBER, "nikke_demo", "metric", "skill_1", 1, is_enabled=False)
    cleared = Override(MEMBER, "nikke_demo", "metric", "skill_1", operation="clear")
    assert dict(apply_overrides((record,), (zero,))[0].metrics) == {"skill_1": 0}
    assert dict(apply_overrides((record,), (blank,))[0].metrics) == {"skill_1": None}
    assert apply_overrides((record,), (disabled,)) == (record,)
    assert apply_overrides((record,), (cleared,)) == (record,)
    assert dict(record.metrics) == {"skill_1": 10}
    with pytest.raises(ValueError, match="duplicate override"):
        apply_overrides((record,), (zero, blank))
    with pytest.raises(ValueError, match="coverage"):
        apply_overrides((), (zero,))
    with pytest.raises(ValueError, match="ownership"):
        apply_overrides((TrainingRecord(MEMBER, "nikke_demo", "unknown"),), (zero,))


def test_changes_use_common_metric_ids_and_verified_ownership():
    before = capture_training("union_demo", WHEN, "reference_demo", (
        TrainingRecord(MEMBER, "nikke_demo", "owned", {"skill_1": 9, "missing": None}, {"attack_demo": 10}),
        TrainingRecord(MEMBER, "missing_demo", "not_owned"),
        TrainingRecord(MEMBER, "unknown_demo", "unknown"),
    ))
    after = capture_training("union_demo", WHEN, "reference_demo", (
        TrainingRecord(MEMBER, "nikke_demo", "owned", {"skill_1": 10, "missing": 8}, {"attack_demo": 15, "new_demo": 20}),
        TrainingRecord(MEMBER, "missing_demo", "owned"),
        TrainingRecord(MEMBER, "unknown_demo", "owned"),
    ))
    changes = diff_training(before, after)
    assert [(item.namespace, item.field_id, item.before, item.after) for item in changes] == [
        ("ownership", "ownership_status", "not_owned", "owned"),
        ("metric", "skill_1", 9, 10), ("function", "attack_demo", 10, 15),
    ]
    assert diff_training(before, before) == ()
    other = capture_training("another_union", WHEN, "reference_demo", ())
    with pytest.raises(ValueError, match="different unions"):
        diff_training(before, other)


def test_equivalent_numbers_and_unknown_values_are_not_false_progress():
    before = capture_training("union_demo", WHEN, "reference_demo", (
        TrainingRecord(MEMBER, "nikke_demo", "owned", {"skill_1": "", "collection": "R0"}, {"attack_demo": "10.00"}),
    ))
    after = capture_training("union_demo", WHEN, "reference_demo", (
        TrainingRecord(MEMBER, "nikke_demo", "owned", {"skill_1": 10, "collection": 1}, {"attack_demo": 10}),
    ))
    assert diff_training(before, after) == ()
