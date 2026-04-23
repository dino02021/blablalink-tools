# -*- coding: utf-8 -*-

from lib import data_store


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
