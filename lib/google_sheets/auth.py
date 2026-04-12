# -*- coding: utf-8 -*-
"""Google Sheets 認證：建立 gspread client。"""

import json
from pathlib import Path
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

from ..constants import GOOGLE_CRED_FILE, GOOGLE_SCOPES


def get_gspread_client(cred_path: Optional[Path] = None) -> gspread.client.Client:
    """
    讀取 service account 憑證，建立並回傳 gspread client。
    cred_path 預設為 constants.GOOGLE_CRED_FILE。
    """
    path = cred_path or GOOGLE_CRED_FILE
    if not path.exists():
        raise FileNotFoundError(f"service account json not found: {path}")

    creds_dict = json.loads(path.read_text(encoding="utf-8"))
    creds = Credentials.from_service_account_info(creds_dict, scopes=GOOGLE_SCOPES)
    return gspread.authorize(creds)
