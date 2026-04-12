# -*- coding: utf-8 -*-
"""Google Sheets 基礎操作封裝。"""

from typing import List, Optional
from datetime import datetime

import gspread

from .auth import get_gspread_client
from ..log import log_exc
from gspread.utils import rowcol_to_a1


def list_worksheet_titles(sheet_id: str, client: Optional[gspread.client.Client] = None) -> Optional[List[str]]:
    """取得試算表所有工作表標題，失敗回傳 None。"""
    try:
        cli = client or get_gspread_client()
        sh = cli.open_by_key(sheet_id)
        return [ws.title for ws in sh.worksheets()]
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return None


def update_cells(ws: gspread.Worksheet, start_row: int, start_col: int, values: List[List[str]], value_input_option: str = "RAW") -> bool:
    """依起始列行寫入矩形資料。"""
    try:
        end_row = start_row + len(values) - 1
        end_col = start_col + len(values[0]) - 1
        rng = f"{rowcol_to_a1(start_row, start_col)}:{rowcol_to_a1(end_row, end_col)}"
        ws.update(rng, values, value_input_option=value_input_option)
        return True
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return False


def read_cells(ws: gspread.Worksheet, cell_range: str) -> Optional[List[List[str]]]:
    """讀取指定範圍。"""
    try:
        return ws.get(cell_range)
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return None


def append_rows(
    sheet_id: str,
    worksheet_name: str,
    rows: List[List[str]],
    value_input_option: str = "RAW",
    client: Optional[gspread.client.Client] = None,
) -> bool:
    """
    將多行資料附加到指定工作表末尾。
    rows: list of list，對應各欄位值。
    value_input_option: RAW 或 USER_ENTERED。
    成功回傳 True，失敗回傳 False。
    """
    try:
        cli = client or get_gspread_client()
        sh = cli.open_by_key(sheet_id)
        ws = sh.worksheet(worksheet_name)
        ws.append_rows(rows, value_input_option=value_input_option)
        return True
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return False


def update_range(
    sheet_id: str,
    worksheet_name: str,
    cell_range: str,
    values: List[List[str]],
    value_input_option: str = "RAW",
    client: Optional[gspread.client.Client] = None,
) -> bool:
    """
    將資料寫入指定範圍（例如 'A1:C3'）。
    成功回傳 True，失敗回傳 False。
    """
    try:
        cli = client or get_gspread_client()
        sh = cli.open_by_key(sheet_id)
        ws = sh.worksheet(worksheet_name)
        ws.update(cell_range, values, value_input_option=value_input_option)
        return True
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return False


def read_range(
    sheet_id: str,
    worksheet_name: str,
    cell_range: str,
    client: Optional[gspread.client.Client] = None,
) -> Optional[List[List[str]]]:
    """
    讀取指定範圍資料，回傳 list of list；失敗回傳 None。
    """
    try:
        cli = client or get_gspread_client()
        sh = cli.open_by_key(sheet_id)
        ws = sh.worksheet(worksheet_name)
        return ws.get(cell_range)
    except Exception as exc:  # noqa: BLE001
        log_exc(exc)
        return None
