# -*- coding: utf-8 -*-
"""Tests for union raid team image formulas."""

from __future__ import annotations

from lib.gui.dialogs import union_raid_upload


def test_obfuscate_nikke_resource_path_matches_blablalink_paths():
    assert (
        union_raid_upload._obfuscate_nikke_resource_path("character/character_avatar_map.json")
        == "kw-85/7548a00f7a945550ddf0958a80abce46.json"
    )
    assert (
        union_raid_upload._obfuscate_nikke_resource_path("character/si/si_c223_01_s.webp")
        == "od-63/hl-27/f078b4261519b0d1f598b6c1e943469a.webp"
    )
    assert (
        union_raid_upload._obfuscate_nikke_resource_path("character/si/si_c281_01_s.webp")
        == "zl-11/wg-66/3ac85e36766abc5e052921bbe2b857ae.webp"
    )


def test_build_union_raid_team_image_formula_uses_existing_cloudinary_style():
    icon_url = union_raid_upload._build_nikke_icon_url(223, 1)

    formula = union_raid_upload._build_union_raid_team_image_formula(
        [{"squad_id": [5515, 9999], "step": 1}],
        {5515: icon_url},
    )

    assert formula.startswith('=IFERROR(IMAGE("https://res.cloudinary.com/dfzoxtcki/image/upload/')
    assert "c_pad,w_180,h_36,g_north_west,b_transparent" in formula
    assert "fl_layer_apply,g_north_west,x_0,y_0" in formula
    assert "images.weserv.nl" in formula
    assert "sg-tools-cdn.blablalink.com%2F" in formula


def test_avatar_map_does_not_override_nikke_info_short_ids(monkeypatch):
    cloud_items = [
        {
            "id": 3015,
            "name": "阿妮斯：閃耀夏日",
            "resource_id": None,
        },
        {
            "id": 3017,
            "name": "阿妮斯：超級巨星",
            "resource_id": None,
        },
    ]
    remote_items = [
        {
            "id": 301501,
            "name_localkey": {"name": "阿妮斯：閃耀夏日"},
            "resource_id": 15,
        },
        {
            "id": 301701,
            "name_localkey": {"name": "阿妮斯：超級巨星"},
            "resource_id": 17,
        },
        {
            "id": 583601,
            "name_localkey": {"name": "櫻"},
            "resource_id": 836,
        },
        {
            "id": 584201,
            "name_localkey": {"name": "克蕾兒"},
            "resource_id": 842,
        },
    ]
    avatar_items = [
        {"id": 301500, "resource_id": 836, "costume_index": 0},
        {"id": 301700, "resource_id": 842, "costume_index": 0},
    ]

    def fake_fetch_json_list(url):
        if url == union_raid_upload.NIKKES_INFO_URL:
            return remote_items
        return avatar_items

    monkeypatch.setattr(union_raid_upload, "read_cloud_data_json", lambda *_args, **_kwargs: cloud_items)
    monkeypatch.setattr(union_raid_upload, "_fetch_json_list", fake_fetch_json_list)

    _id_to_name, id_to_image_url = union_raid_upload._load_nikke_upload_maps()

    assert id_to_image_url[3015] == union_raid_upload._build_nikke_icon_url(15, 0)
    assert id_to_image_url[3017] == union_raid_upload._build_nikke_icon_url(17, 0)
    assert id_to_image_url[301500] == union_raid_upload._build_nikke_icon_url(836, 0)
    assert id_to_image_url[301700] == union_raid_upload._build_nikke_icon_url(842, 0)


def test_apply_formula_updates_uses_user_entered():
    calls = []

    class FakeWorksheet:
        def batch_update(self, payload, value_input_option=None):
            calls.append((payload, value_input_option))

    payload = [{"range": "C3:C3", "values": [["=IMAGE(\"https://example.test/a.png\")"]]}]

    union_raid_upload._apply_formula_updates(FakeWorksheet(), payload)

    assert calls == [(payload, "USER_ENTERED")]
