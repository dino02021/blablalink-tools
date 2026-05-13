# -*- coding: utf-8 -*-

import json

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
        lambda: {1001: {"name_code": 1001, "element": "Fire"}},
    )
    monkeypatch.setattr(
        data_store,
        "write_data_text_and_sync_if_changed",
        lambda path, content, encoding="utf-8": saved.setdefault("content", content) is not None,
    )

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
    assert json.loads(saved["content"]) == [
        {
            "name_code": 1001,
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
            {"name_code": 1001, "type": member_type, "function1": "優越代碼傷害", "function2": "攻擊力"},
        ],
    )
    monkeypatch.setattr(data_store, "_build_nikke_info_lookup", lambda: {1001: {"element": "Fire"}})

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
        lambda value: saved.setdefault("items", value) is not None,
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
