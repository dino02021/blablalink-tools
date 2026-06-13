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


def test_apply_formula_updates_uses_user_entered():
    calls = []

    class FakeWorksheet:
        def batch_update(self, payload, value_input_option=None):
            calls.append((payload, value_input_option))

    payload = [{"range": "C3:C3", "values": [["=IMAGE(\"https://example.test/a.png\")"]]}]

    union_raid_upload._apply_formula_updates(FakeWorksheet(), payload)

    assert calls == [(payload, "USER_ENTERED")]
