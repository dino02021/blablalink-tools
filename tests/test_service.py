# -*- coding: utf-8 -*-
"""針對 google_sheets.service 的單元測試（使用假 client/ws，不打 Google API）。"""

import pytest

from lib.google_sheets import service


class FakeWs:
    def __init__(self, name="ws"):
        self.title = name
        self.updated = None
        self.appended = None
        self.got = None

    def update(self, rng, vals, value_input_option="RAW"):
        self.updated = (rng, vals, value_input_option)

    def append_rows(self, rows, value_input_option="RAW"):
        self.appended = (rows, value_input_option)

    def get(self, cell_range):
        self.got = cell_range
        return [["ok"]]


class FakeSh:
    def __init__(self, worksheets=None, ws=None):
        self._worksheets = worksheets or []
        self._ws = ws or FakeWs()

    def worksheets(self):
        return self._worksheets

    def worksheet(self, name):
        return self._ws


class FakeCli:
    def __init__(self, sh=None):
        self._sh = sh or FakeSh()
        self.opened = None

    def open_by_key(self, key):
        self.opened = key
        return self._sh


def test_list_worksheet_titles(monkeypatch):
    ws1 = FakeWs("A")
    ws2 = FakeWs("B")
    sh = FakeSh(worksheets=[ws1, ws2])
    cli = FakeCli(sh)
    monkeypatch.setattr(service, "get_gspread_client", lambda: cli)

    titles = service.list_worksheet_titles("sheet123")
    assert titles == ["A", "B"]


def test_list_worksheet_titles_error(monkeypatch):
    monkeypatch.setattr(service, "get_gspread_client", lambda: (_ for _ in ()).throw(Exception("boom")))
    assert service.list_worksheet_titles("sheet123") is None


def test_update_cells_success():
    ws = FakeWs()
    vals = [["x", "y"]]
    ok = service.update_cells(ws, 1, 1, vals)
    assert ok is True
    rng, got_vals, opt = ws.updated
    assert rng == "A1:B1"
    assert got_vals == vals
    assert opt == "RAW"


def test_update_cells_error(monkeypatch):
    class BadWs(FakeWs):
        def update(self, rng, vals, value_input_option="RAW"):
            raise Exception("fail")

    ws = BadWs()
    assert service.update_cells(ws, 1, 1, [[1]]) is False


def test_append_rows_success(monkeypatch):
    ws = FakeWs()
    sh = FakeSh(ws=ws)
    cli = FakeCli(sh)
    monkeypatch.setattr(service, "get_gspread_client", lambda: cli)

    ok = service.append_rows("sid", "ws", [[1, 2]])
    assert ok is True
    assert ws.appended == ([[1, 2]], "RAW")
    assert cli.opened == "sid"


def test_append_rows_error(monkeypatch):
    monkeypatch.setattr(service, "get_gspread_client", lambda: (_ for _ in ()).throw(Exception("boom")))
    assert service.append_rows("sid", "ws", [[1]]) is False


def test_read_range_success(monkeypatch):
    ws = FakeWs()
    sh = FakeSh(ws=ws)
    cli = FakeCli(sh)
    monkeypatch.setattr(service, "get_gspread_client", lambda: cli)

    out = service.read_range("sid", "ws", "A1:B2")
    assert out == [["ok"]]
    assert ws.got == "A1:B2"


def test_read_range_error(monkeypatch):
    monkeypatch.setattr(service, "get_gspread_client", lambda: (_ for _ in ()).throw(Exception("boom")))
    assert service.read_range("sid", "ws", "A1:B2") is None
