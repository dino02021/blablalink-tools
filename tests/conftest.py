# -*- coding: utf-8 -*-
"""共用測試設定：將專案根目錄加入 sys.path，並隔離真實資料檔。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolate_settings_files(tmp_path_factory):
    """避免測試讀寫到真實使用者設定檔。"""
    tmp_root = tmp_path_factory.mktemp("settings")
    settings_dir = tmp_root / "BlaBlaLinkToolSettings"
    settings_dir.mkdir(parents=True, exist_ok=True)

    from lib import constants as c
    from lib import utils

    c.SETTINGS_DIR = settings_dir
    c.ACCOUNTS_FILE = settings_dir / "accounts.json"
    c.SESSIONS_FILE = settings_dir / "sessions.json"
    c.GOOGLE_SHEETS_ID_FILE = settings_dir / "google_sheets_id.json"
    c.MEMBERS_ID_FILE = settings_dir / "members_id.json"
    c.GOOGLE_CRED_FILE = settings_dir / "service_account.json"

    utils.ACCOUNTS_FILE = c.ACCOUNTS_FILE
    utils.SESSIONS_FILE = c.SESSIONS_FILE
    utils.DATA_DIR = tmp_root

    yield
