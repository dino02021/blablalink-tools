# -*- coding: utf-8 -*-
"""通用 UI 介面設定工具函式。"""

from __future__ import annotations

import functools
import tkinter as tk
from tkinter import ttk
from typing import Any

from . import ui_constants as ui


def close_dialog(dlg: tk.Misc) -> None:
    """釋放 grab 並關閉對話框（忽略例外）。"""
    try:
        dlg.grab_release()
    except Exception:
        pass
    try:
        dlg.destroy()
    except Exception:
        pass


def place_on_same_screen(parent: tk.Misc, dlg: tk.Toplevel, width: int, height: int) -> None:
    """
    將對話框放在與 parent 同一個螢幕，盡量置中顯示。
    """
    prev_state = dlg.state()
    dlg.withdraw()  # 先隱藏，避免極小尺寸閃爍
    try:
        if parent:
            parent.update_idletasks()
            px, py = parent.winfo_rootx(), parent.winfo_rooty()
            pw, ph = parent.winfo_width(), parent.winfo_height()
            if pw <= 1 or ph <= 1:
                pw, ph = parent.winfo_reqwidth(), parent.winfo_reqheight()
        else:
            px = py = 0
            pw, ph = dlg.winfo_screenwidth(), dlg.winfo_screenheight()

        pos_x = px + max((pw - width) // 2, 0)
        pos_y = py + max((ph - height) // 2, 0)
        dlg.geometry(f"{width}x{height}+{pos_x}+{pos_y}")
    except Exception:
        dlg.geometry(f"{width}x{height}")
    finally:
        try:
            dlg.deiconify()
            if prev_state == "iconic":
                dlg.iconify()
        except Exception:
            pass


def create_dialog(
    parent: tk.Misc,
    title: str,
    width: int,
    height: int,
    **dialog_kwargs: Any,
) -> tk.Toplevel:
    """
    建立並置中的 Toplevel 對話框並套用常用設定。
    """
    dlg = tk.Toplevel(parent)
    dlg.title(title)
    if "resizable" not in dialog_kwargs:
        dialog_kwargs["resizable"] = False
    if "transient" not in dialog_kwargs:
        dialog_kwargs["transient"] = True
    if "grab" not in dialog_kwargs:
        dialog_kwargs["grab"] = True

    resizable = dialog_kwargs.pop("resizable")
    if isinstance(resizable, (tuple, list)) and len(resizable) == 2:
        dlg.resizable(bool(resizable[0]), bool(resizable[1]))
    else:
        dlg.resizable(bool(resizable), bool(resizable))
    if dialog_kwargs.pop("transient", True):
        dlg.transient(parent)
    if dialog_kwargs.pop("grab", True):
        dlg.grab_set()
    place_on_same_screen(parent, dlg, width, height)
    return dlg


def create_frame(parent: tk.Misc, **pack_kwargs: Any) -> ttk.Frame:
    """
    建立外層 Frame（固定使用 FRAME_PAD 並 pack 滿版）。
    其餘 pack 參數可透過 pack_kwargs 補上。
    """
    frame = ttk.Frame(parent, padding=ui.FRAME_PAD)
    if "padx" not in pack_kwargs:
        pack_kwargs["padx"] = ui.FRAME_PAD
    if "pady" not in pack_kwargs:
        pack_kwargs["pady"] = ui.FRAME_PAD
    if "fill" not in pack_kwargs:
        pack_kwargs["fill"] = "both"
    if "expand" not in pack_kwargs:
        pack_kwargs["expand"] = True
    frame.pack(**pack_kwargs)
    frame.columnconfigure(0, weight=1)
    return frame


def create_btn_frame(parent: tk.Misc, **grid_kwargs: Any) -> ttk.Frame:
    """
    建立按鈕 Frame（固定使用 BTN_FRAME_PAD 並套用 grid 間距）。
    其餘 grid 參數可透過 grid_kwargs 覆寫。
    """
    frame = ttk.Frame(parent, padding=ui.BTN_FRAME_PAD)
    if "sticky" not in grid_kwargs:
        grid_kwargs["sticky"] = "ew"
    if "padx" not in grid_kwargs:
        grid_kwargs["padx"] = ui.BTN_GRID_PADX
    if "pady" not in grid_kwargs:
        grid_kwargs["pady"] = ui.BTN_GRID_PADY
    frame.grid(**grid_kwargs)
    return frame


def create_btn_between(
    parent: tk.Misc,
    text: str,
    command,
    **grid_kwargs: Any,
) -> ttk.Button:
    """
    建立中間按鈕（套用 BTN_PADX_BETWEEN）。
    其餘 grid 參數可選。
    """
    btn = ttk.Button(parent, text=text, command=command)
    if "padx" not in grid_kwargs:
        grid_kwargs["padx"] = ui.BTN_PADX_BETWEEN
    btn.grid(**grid_kwargs)
    return btn


def create_btn_last(
    parent: tk.Misc,
    text: str,
    command,
    **grid_kwargs: Any,
) -> ttk.Button:
    """
    建立最後一顆按鈕（套用 BTN_PADX_LAST）。
    其餘 grid 參數可選。
    """
    btn = ttk.Button(parent, text=text, command=command)
    if "padx" not in grid_kwargs:
        grid_kwargs["padx"] = ui.BTN_PADX_LAST
    btn.grid(**grid_kwargs)
    return btn


def create_msg_label(
    parent: tk.Misc,
    textvariable: tk.Variable,
    **grid_kwargs: Any,
) -> ttk.Label:
    """
    建立訊息 Label（套用 MSG_PADX/MSG_PADY）。
    其餘 grid 參數可選。
    """
    label = ttk.Label(parent, textvariable=textvariable)
    if "padx" not in grid_kwargs:
        grid_kwargs["padx"] = ui.MSG_PADX
    if "pady" not in grid_kwargs:
        grid_kwargs["pady"] = ui.MSG_PADY
    label.grid(**grid_kwargs)
    return label


def create_entry_label(
    parent: tk.Misc,
    text: str,
    **grid_kwargs: Any,
) -> ttk.Label:
    """
    建立輸入框標籤 Label（套用 ENTRY_LABEL_PADX/ENTRY_LABEL_PADY + sticky="w"）。
    """
    label = ttk.Label(parent, text=text)
    if "sticky" not in grid_kwargs:
        grid_kwargs["sticky"] = "w"
    if "padx" not in grid_kwargs:
        grid_kwargs["padx"] = ui.ENTRY_LABEL_PADX
    if "pady" not in grid_kwargs:
        grid_kwargs["pady"] = ui.ENTRY_LABEL_PADY
    label.grid(**grid_kwargs)
    return label


def create_entry(
    parent: tk.Misc,
    textvariable: tk.Variable,
    show: str | None = None,
    **grid_kwargs: Any,
) -> ttk.Entry:
    """
    建立輸入框（套用 sticky="ew" + ENTRY_PADX/ENTRY_PADY）。
    """
    if show is None:
        entry = ttk.Entry(parent, textvariable=textvariable)
    else:
        entry = ttk.Entry(parent, textvariable=textvariable, show=show)
    if "sticky" not in grid_kwargs:
        grid_kwargs["sticky"] = "ew"
    if "padx" not in grid_kwargs:
        grid_kwargs["padx"] = ui.ENTRY_PADX
    if "pady" not in grid_kwargs:
        grid_kwargs["pady"] = ui.ENTRY_PADY
    entry.grid(**grid_kwargs)
    return entry


def create_combobox(
    parent: tk.Misc,
    textvariable: tk.Variable,
    values: list[str],
    **grid_kwargs: Any,
) -> ttk.Combobox:
    """
    建立 ComboBox（固定 readonly + sticky="ew"）。
    """
    combo = ttk.Combobox(parent, textvariable=textvariable, values=values, state="readonly")
    if "sticky" not in grid_kwargs:
        grid_kwargs["sticky"] = "ew"
    if "padx" not in grid_kwargs:
        grid_kwargs["padx"] = ui.CB_PADX
    if "pady" not in grid_kwargs:
        grid_kwargs["pady"] = ui.CB_PADY
    combo.grid(**grid_kwargs)
    return combo


def create_listbox(parent: tk.Misc, **grid_kwargs: Any) -> tk.Listbox:
    """
    建立 Listbox（dotbox/不共用選擇/置滿 + LIST padding）。
    """
    lb = tk.Listbox(parent, activestyle="dotbox", exportselection=False)
    lb.configure(selectborderwidth=0)
    if "sticky" not in grid_kwargs:
        grid_kwargs["sticky"] = "nsew"
    if "padx" not in grid_kwargs:
        grid_kwargs["padx"] = ui.LIST_PADX
    if "pady" not in grid_kwargs:
        grid_kwargs["pady"] = ui.LIST_PADY
    lb.grid(**grid_kwargs)
    return lb


def create_entry_frame(parent: tk.Misc, **grid_kwargs: Any) -> ttk.Frame:
    """
    建立輸入框區塊 Frame（固定使用 ENTRY_FRAME_PAD 並套用 grid 間距）。
    其餘 grid 參數可覆寫。
    """
    frame = ttk.Frame(parent, padding=ui.ENTRY_FRAME_PAD)
    if "sticky" not in grid_kwargs:
        grid_kwargs["sticky"] = "ew"
    if "padx" not in grid_kwargs:
        grid_kwargs["padx"] = ui.ENTRY_GRID_PADX
    if "pady" not in grid_kwargs:
        grid_kwargs["pady"] = ui.ENTRY_GRID_PADY
    frame.grid(**grid_kwargs)
    frame.columnconfigure(1, weight=1)
    return frame


def confirm_dialog(parent: tk.Misc, message: str, title: str = "確認刪除") -> bool:
    """顯示確認對話框，回傳 True/False。"""
    dlg = create_dialog(parent, title, 260, 120)

    frame = create_frame(dlg)
    frame.rowconfigure(0, weight=1)
    label = create_entry_label(frame, message, row=0, column=0, sticky="nsew")
    label.configure(anchor="center", justify="center")

    btns = create_btn_frame(frame, row=1, column=0)
    btns.columnconfigure(0, weight=1)
    btns.columnconfigure(1, weight=1)

    result = {"ok": False}
    create_btn_between(
        btns,
        "確定",
        functools.partial(_confirm_set_and_close, dlg, result, True),
        row=0,
        column=0,
        sticky="we",
    )
    create_btn_last(
        btns,
        "取消",
        functools.partial(_confirm_set_and_close, dlg, result, False),
        row=0,
        column=1,
        sticky="we",
    )
    dlg.protocol("WM_DELETE_WINDOW", functools.partial(_confirm_set_and_close, dlg, result, False))
    parent.wait_window(dlg)
    return result["ok"]


def _confirm_set_and_close(dlg: tk.Toplevel, result: dict[str, bool], value: bool) -> None:
    result["ok"] = value
    close_dialog(dlg)
