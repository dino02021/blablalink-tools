# -*- coding: utf-8 -*-

import pytest

from lib import data_store


def test_load_healthcheck_function_names_normalizes_cloud_names(monkeypatch):
    monkeypatch.setattr(
        data_store,
        "read_cloud_data_json",
        lambda path, default=None: [
            {"function_name": "【優越代碼傷害增加】", "function_id": 70001},
            {"function_name": " 攻擊力 增加 ", "function_id": 70002},
            {"function_name": "【優越代碼傷害增加】", "function_id": 70003},
            {"function_name": ""},
        ],
    )

    assert data_store.load_healthcheck_function_names() == ["優越代碼傷害", "攻擊力"]


def test_save_healthcheck_members_preserves_function_fields(monkeypatch):
    member_type = data_store.HEALTHCHECK_TYPE_VALUES[0]
    saved = {}
    monkeypatch.setattr(
        data_store,
        "_build_nikke_info_lookup",
        lambda: {1001: {"name_code": 1001, "name": "Alice", "element": "Fire"}},
    )
    monkeypatch.setattr(
        data_store,
        "write_healthcheck_rows",
        lambda rows, expected_rows=None: saved.setdefault("rows", rows),
    )
    monkeypatch.setattr(data_store, "load_healthcheck_function_definitions", lambda: [
        {"name": "優越代碼傷害", "function_id": 70005}, {"name": "攻擊力", "function_id": 70008},
    ])

    ok = data_store.save_healthcheck_members(
        [
            {
                "name_code": 1001,
                "type": member_type,
                "function1": "【優越代碼傷害增加】",
                "function2": "優越代碼傷害",
                "function3": " 攻擊力 增加 ",
            }
        ]
    )

    assert ok is True
    assert saved["rows"] == [
        {
            "name": "Alice",
            "element": data_store.translate_nikke_element("Fire"),
            "type": member_type,
            "function1": "優越代碼傷害",
            "function3": "攻擊力",
        }
    ]


def test_build_healthcheck_function_id_targets_matches_selected_text(monkeypatch):
    member_type = data_store.HEALTHCHECK_TYPE_VALUES[0]
    monkeypatch.setattr(
        data_store,
        "read_cloud_data_json",
        lambda path, default=None: [
            {"function_name": "【優越代碼傷害增加】", "function_id": 70005},
            {"function_name": "攻擊力增加", "function_id": 70007},
        ],
    )
    monkeypatch.setattr(data_store, "read_healthcheck_rows", lambda: [
        {"name": "Alice", "element": "燃燒", "type": member_type, "function1": "優越代碼傷害", "function2": "攻擊力"},
    ])
    monkeypatch.setattr(data_store, "_build_nikke_info_lookup", lambda: {1001: {"name": "Alice", "element": "Fire"}})

    assert data_store.build_healthcheck_function_id_targets() == {"1001": [70005, 70007, None]}


def test_delete_healthcheck_member_by_index(monkeypatch):
    items = [
        {"name_code": 1001, "type": "A", "element": "火"},
        {"name_code": 1002, "type": "B", "element": "水"},
        {"name_code": 1003, "type": "C", "element": "風"},
    ]
    saved = {}

    monkeypatch.setattr(data_store, "load_healthcheck_members", lambda: [dict(item) for item in items])
    monkeypatch.setattr(
        data_store,
        "save_healthcheck_members",
        lambda value, expected_members=None: saved.setdefault("items", value) is not None,
    )

    ok = data_store.delete_healthcheck_member_by_index(1)

    assert ok is True
    assert saved["items"] == [
        {"name_code": 1001, "type": "A", "element": "火"},
        {"name_code": 1003, "type": "C", "element": "風"},
    ]


def test_delete_healthcheck_member_by_index_out_of_range(monkeypatch):
    monkeypatch.setattr(data_store, "load_healthcheck_members", lambda: [{"name_code": 1001, "type": "A"}])
    called = {"saved": False}
    monkeypatch.setattr(data_store, "save_healthcheck_members", lambda value: called.__setitem__("saved", True))

    ok = data_store.delete_healthcheck_member_by_index(5)

    assert ok is False
    assert called["saved"] is False


def test_sheet_read_failure_never_falls_back_to_cloud_members(monkeypatch):
    monkeypatch.setattr(data_store, "read_healthcheck_rows", lambda: (_ for _ in ()).throw(ValueError("sheet unavailable")))
    monkeypatch.setattr(data_store, "read_cloud_data_json", lambda *args, **kwargs: pytest.fail("unexpected cloud fallback"))
    with pytest.raises(ValueError, match="sheet unavailable"):
        data_store.load_healthcheck_members()


@pytest.mark.parametrize("rows", [
    [{"name": "Unknown", "element": "燃燒", "type": "輸出"}],
    [{"name": "Alice", "element": "bad", "type": "輸出"}],
    [{"name": "Alice", "element": "", "type": "輸出"}],
    [{"name": "Alice", "element": "燃燒", "type": "bad"}],
    [{"name": "Alice", "element": "燃燒", "type": "輸出", "function1": "攻擊力", "function2": "攻擊力"}],
    [{"name": "Alice", "element": "燃燒", "type": "輸出"}] * 2,
])
def test_invalid_sheet_rows_are_rejected_instead_of_silently_skipped(monkeypatch, rows):
    monkeypatch.setattr(data_store, "_build_nikke_info_lookup", lambda: {1001: {"name": "Alice", "element": "Fire"}})
    with pytest.raises(ValueError):
        data_store.normalize_healthcheck_rows(rows)


def test_trait_mapping_does_not_accept_partial_names(monkeypatch):
    monkeypatch.setattr(data_store, "load_healthcheck_function_definitions", lambda: [{"name": "攻擊力", "function_id": 70008}])
    with pytest.raises(ValueError, match="function_id"):
        data_store.build_healthcheck_function_id_targets([{"name_code": 1001, "function1": "攻擊"}])


def test_same_role_with_conflicting_traits_is_rejected(monkeypatch):
    monkeypatch.setattr(data_store, "load_healthcheck_function_definitions", lambda: [{"name": "攻擊力", "function_id": 70008}])
    with pytest.raises(ValueError, match="不同詞條"):
        data_store.build_healthcheck_function_id_targets([
            {"name_code": 1001, "type": "輸出", "function1": "攻擊力"},
            {"name_code": 1001, "type": "輔助"},
        ])


def test_delete_detects_reordered_sheet_before_removing_a_different_role(monkeypatch):
    monkeypatch.setattr(data_store, "load_healthcheck_members", lambda: [{"name_code": 1002, "type": "輸出"}])
    monkeypatch.setattr(data_store, "save_healthcheck_members", lambda *args, **kwargs: pytest.fail("must not save"))
    with pytest.raises(ValueError, match="已變更"):
        data_store.delete_healthcheck_member_by_index(0, expected_member={"name_code": 1001, "type": "輸出"})
