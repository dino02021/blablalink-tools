# -*- coding: utf-8 -*-
"""針對 lib.utils 的單元測試（不碰正式資料夾，全部用 tmp_path/monkeypatch）。"""

import json

import pytest

from lib import utils


def test_get_nested_value():
    obj = {"a": {"b": {"c": 1}}}
    assert utils.get_nested_value(obj, "a", "b", "c") == 1
    assert utils.get_nested_value(obj, "a", "x") is None
    assert utils.get_nested_value("not_dict", "a") is None


def test_xor_crypt_roundtrip():
    text = "hello"
    key = "k"
    token = utils._xor_crypt(text, key)
    assert utils._xor_decrypt(token, key) == text


def test_save_and_load_session(tmp_path, monkeypatch):
    sess_file = tmp_path / "sessions.json"
    monkeypatch.setattr(utils, "SESSIONS_FILE", sess_file)
    monkeypatch.setattr(utils, "DATA_DIR", tmp_path)

    state = {"cookies": [{"name": "c", "value": "v"}]}
    utils.save_session("acc1", state)
    loaded = utils.load_session("acc1")
    assert loaded == state


def test_save_and_load_accounts(tmp_path, monkeypatch):
    acc_file = tmp_path / "accounts.json"
    monkeypatch.setattr(utils, "ACCOUNTS_FILE", acc_file)
    monkeypatch.setattr(utils, "DATA_DIR", tmp_path)

    accounts = [{"nickname": "nick", "username": "u", "password": "p"}]
    utils.save_accounts(accounts)

    # 檔案存在且內容被加密
    raw = json.loads(acc_file.read_text(encoding="utf-8"))
    assert raw[0]["password"] != "p"

    loaded = utils.load_accounts()
    assert loaded == accounts
