# -*- coding: utf-8 -*-
"""檢查 GUI 共用常數是否存在且型別合理。"""
from lib.gui import ui_constants as ui


def test_ui_constants_shapes():
    # FRAME/LIST padding
    assert isinstance(ui.FRAME_PAD, int)
    assert isinstance(ui.LIST_PADX, int)
    assert isinstance(ui.LIST_PADY, tuple) and len(ui.LIST_PADY) == 2
    # 按鈕 padding
    assert isinstance(ui.BTN_FRAME_PAD, tuple) and len(ui.BTN_FRAME_PAD) == 4
    assert isinstance(ui.BTN_GRID_PADX, int)
    assert isinstance(ui.BTN_GRID_PADY, tuple) and len(ui.BTN_GRID_PADY) == 2
    assert isinstance(ui.BTN_PADX_BETWEEN, tuple) and len(ui.BTN_PADX_BETWEEN) == 2
    assert isinstance(ui.BTN_PADX_LAST, tuple) and len(ui.BTN_PADX_LAST) == 2
    # label / message padding
    assert isinstance(ui.LABEL_PADX, tuple) and len(ui.LABEL_PADX) == 2
    assert isinstance(ui.LABEL_PADY, tuple) and len(ui.LABEL_PADY) == 2
    assert isinstance(ui.MSG_PADX, int)
    assert isinstance(ui.MSG_PADY, tuple) and len(ui.MSG_PADY) == 2
