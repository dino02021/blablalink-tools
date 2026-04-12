# -*- coding: utf-8 -*-
"""確認常數存在且基本型別正確，不做 I/O。"""
from pathlib import Path

import lib.constants as c


def test_constant_paths_are_str_or_pathlike():
    path_consts = [
        c.DATA_DIR,
        c.LOG_DIR,
        c.ACCOUNTS_FILE,
        c.SESSIONS_FILE,
        c.NIKKES_INFO_FILE,
        c.FUNCTION_NAMES_FILE,
        c.GOOGLE_SHEETS_ID_FILE,
    ]
    assert all(isinstance(p, (str, Path)) for p in path_consts)


def test_urls_are_strings():
    url_consts = [
        c.ROLEINFO_URL,
        c.CHECKLOGIN_URL,
        c.NIKKES_INFO_URL,
        c.FUNCTION_NAMES_URL,
    ]
    assert all(isinstance(u, str) and u.startswith("http") for u in url_consts)
