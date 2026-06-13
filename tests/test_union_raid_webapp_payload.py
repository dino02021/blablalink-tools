# -*- coding: utf-8 -*-
"""Tests for union raid WebApp notification targeting."""

from __future__ import annotations

from lib.gui.dialogs import union_raid_upload


def test_resolve_raid_plan_sheet_for_union_upload_maps_union_raid_to_character_sheet(monkeypatch):
    monkeypatch.setattr(
        union_raid_upload,
        "load_sheets",
        lambda: (
            [
                {
                    "type": "UnionRaid",
                    "union_id": "3097",
                    "sheet_id": "damage-sheet",
                    "sheet_name": "月下傷害",
                },
                {
                    "type": "CharacterDetails",
                    "union_id": "3097",
                    "sheet_id": "plan-sheet",
                    "sheet_name": "月下管理",
                },
            ],
            None,
        ),
    )

    sheet_id, union_id = union_raid_upload._resolve_raid_plan_sheet_for_union_upload("damage-sheet")

    assert sheet_id == "plan-sheet"
    assert union_id == "3097"


def test_notify_union_web_app_uses_plan_sheet_and_keeps_damage_sheet_metadata(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200
        text = '{"ok":true}'

        @staticmethod
        def json():
            return {"ok": True}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(union_raid_upload, "UNION_WEB_APP_URL", "https://example.test/exec")

    import requests

    monkeypatch.setattr(requests, "post", fake_post)

    ok, msg = union_raid_upload._notify_union_web_app(
        "2026/06",
        "plan-sheet",
        [{"name": "安ANN"}],
        damage_sheet_id="damage-sheet",
        union_id="3097",
    )

    assert ok is True
    assert msg == '{"ok":true}'
    assert captured["url"] == "https://example.test/exec"
    assert captured["timeout"] == 600
    assert captured["json"]["method"] == "sync_union_raid_teams"
    assert captured["json"]["spreadsheet_id"] == "plan-sheet"
    assert captured["json"]["damage_spreadsheet_id"] == "damage-sheet"
    assert captured["json"]["union_id"] == "3097"
