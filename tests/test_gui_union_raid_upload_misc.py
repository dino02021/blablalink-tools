# -*- coding: utf-8 -*-
"""針對 union_raid_upload 的純計算函式做基本測試。"""
import pytest

from lib.utils import col_letter


def test_col_letter_basic():
    assert col_letter(1) == "A"
    assert col_letter(26) == "Z"
    assert col_letter(27) == "AA"
    assert col_letter(52) == "AZ"
    assert col_letter(53) == "BA"
    assert col_letter(703) == "AAA"


def test_col_letter_invalid():
    # 非正整數視為空字串轉換預設 "A"
    assert col_letter(0) == "A"
    with pytest.raises(TypeError):
        col_letter(None)  # type: ignore[arg-type]
