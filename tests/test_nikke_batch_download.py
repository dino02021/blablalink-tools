# -*- coding: utf-8 -*-

from lib.jobs import nikke_batch_download


def test_filter_checklist_items_matches_name_code_list():
    checklist = [(1007, "Alice"), (1008, "Bob"), (1009, "Carol")]

    assert nikke_batch_download._filter_checklist_items(
        checklist,
        ["1007", 1009, "bad"],
    ) == [(1007, "Alice"), (1009, "Carol")]


def test_build_user_character_lookup_adds_grade_and_core():
    assert nikke_batch_download._build_user_character_lookup(
        [
            {"name_code": 1007, "grade": 3, "core": 3},
            {"name_code": "1008", "grade": "2", "core": 0},
            {"name_code": 1009, "grade": None, "core": "bad"},
            {"grade": 1, "core": 1},
        ]
    ) == {
        "1007": {"grade": 3, "core": 3, "breakthrough": 6},
        "1008": {"grade": "2", "core": 0, "breakthrough": 2},
        "1009": {"grade": None, "core": "bad", "breakthrough": 0},
    }
