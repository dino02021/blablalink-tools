# -*- coding: utf-8 -*-
"""測試 utils 與 accounts/sessions 載入/儲存的錯誤情境。"""
import json

import pytest

from lib import utils


def test_load_session_nonexistent(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "SESSIONS_FILE", tmp_path / "sessions.json")
    monkeypatch.setattr(utils, "DATA_DIR", tmp_path)
    assert utils.load_session("acc") is None


def test_load_session_invalid_json(tmp_path, monkeypatch):
    sess = tmp_path / "sessions.json"
    sess.write_text("not json", encoding="utf-8")
    monkeypatch.setattr(utils, "SESSIONS_FILE", sess)
    monkeypatch.setattr(utils, "DATA_DIR", tmp_path)
    assert utils.load_session("acc") is None


def test_save_session_overwrite(tmp_path, monkeypatch):
    sess = tmp_path / "sessions.json"
    sess.write_text(json.dumps([{"account": "old", "cookies": [1]}, {"account": "acc", "cookies": [2]}]), encoding="utf-8")
    monkeypatch.setattr(utils, "SESSIONS_FILE", sess)
    monkeypatch.setattr(utils, "DATA_DIR", tmp_path)

    utils.save_session("acc", {"cookies": [3]})
    data = json.loads(sess.read_text(encoding="utf-8"))
    # acc 應被覆蓋成新的 cookies
    found = [item for item in data if item.get("account") == "acc"]
    assert found and found[0].get("cookies") == [3]


def test_load_accounts_invalid_json(tmp_path, monkeypatch):
    acc_file = tmp_path / "accounts.json"
    acc_file.write_text("not json", encoding="utf-8")
    monkeypatch.setattr(utils, "ACCOUNTS_FILE", acc_file)
    monkeypatch.setattr(utils, "DATA_DIR", tmp_path)
    assert utils.load_accounts() == []


def test_save_accounts_encrypt(tmp_path, monkeypatch):
    acc_file = tmp_path / "accounts.json"
    monkeypatch.setattr(utils, "ACCOUNTS_FILE", acc_file)
    monkeypatch.setattr(utils, "DATA_DIR", tmp_path)

    data = [{"nickname": "a", "username": "u", "password": "p"}]
    utils.save_accounts(data)
    raw = json.loads(acc_file.read_text(encoding="utf-8"))
    assert raw[0]["password"] != "p"
    loaded = utils.load_accounts()
    assert loaded == data
