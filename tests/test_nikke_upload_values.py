# -*- coding: utf-8 -*-

from lib.gui.dialogs import nikke_upload


def test_build_row_values_places_breakthrough_before_skill_levels():
    values = nikke_upload._build_row_values(
        {
            "breakthrough": 6,
            "skill1_lv": 1,
            "skill2_lv": 2,
            "ulti_skill_lv": 3,
            "head_equip_tier": 9,
            "head_equip_lv": 5,
            "arm_equip_tier": 1,
            "arm_equip_lv": 2,
            "torso_equip_tier": 3,
            "torso_equip_lv": 4,
            "leg_equip_tier": 5,
            "leg_equip_lv": 6,
            "favorite_item_type": "SR",
            "favorite_item_lv": 15,
            "functions": [
                {"function_value": 8.0},
                {"function_value": 12.34},
                {"function_value": 7.0},
            ],
        }
    )

    assert values == [6, 1, 2, 3, "T9", 2, 4, 6, "SR15", 8.0, 12.34, 7.0]
    assert nikke_upload._favorite_index() == 8
