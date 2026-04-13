# -*- coding: utf-8 -*-
"""
共用常數：
- 網站與 API 的基礎路徑
- 資料與紀錄檔的路徑
- 儲存密碼時簡單的 XOR key
"""
import tempfile
from pathlib import Path

# 路徑設定
BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
LOG_DIR = PROJECT_ROOT / "logs"
# 專門存放帳號 / Session 的設定資料夾（使用者文件目錄下）
SETTINGS_DIR = Path.home() / "Documents" / "BlablalinkToolSettings"

# 網站 / API
BASE_URL = "https://www.blablalink.com"
LOGIN_URL = f"{BASE_URL}/login"
REGION_TARGET = "香港/澳門/台灣"
CHECKLOGIN_URL = "https://api.blablalink.com/api/user/CheckLogin"
ROLEINFO_URL = "https://api.blablalink.com/api/game/proxy/Game/GetSavedRoleInfo"
UNIONINFO_URL = "https://api.blablalink.com/api/game/proxy/Game/GetMyGuildInfo"
UNION_RAID_URL = "https://api.blablalink.com/api/game/proxy/Game/GetUnionRaidData"
NIKKE_LIST_URL = f"{BASE_URL}/shiftyspad/nikke-list"
NIKKE_COMBAT_URL = f"{BASE_URL}/shiftyspad/nikke-list?type=combat"
NIKKES_INFO_URL = "https://sg-tools-cdn.blablalink.com/jz-26/ww-14/c4619ec83335bcfd7b23e43600520dc7.json"
FUNCTION_NAMES_URL = "https://sg-tools-cdn.blablalink.com/vv-42/b46c877b8d7077e4eba2f3e924550a25.json"
CHAR_DETAILS_URL = "https://api.blablalink.com/api/game/proxy/Game/GetUserCharacterDetails"
USER_CHARACTERS_URL = "https://api.blablalink.com/api/game/proxy/Game/GetUserCharacters"
GUILDMEMBERS_URL = "https://api.blablalink.com/api/game/proxy/Game/GetGuildMembers"
WEB_APP_URL = "https://script.google.com/macros/s/AKfycbwj8chBtKDlDFRhgYpi-9RddoXPRY1YpSCahOUqmcXkIfvdhQ-ClEx3hW9ExnWqPqsF/exec"
UNION_WEB_APP_URL = "https://script.google.com/macros/s/AKfycbxw9NREhzCCj_2D7qubvTQM-iKwD4l2CiCNvBdui7TC58XHM6kLeyU18kuNt_nNcC3vug/exec"

# 檔案路徑
LOG_PATH = LOG_DIR / "blablalinkTool.log"
SESSIONS_FILE = SETTINGS_DIR / "sessions.json"
ACCOUNTS_FILE = SETTINGS_DIR / "accounts.json"
NIKKES_INFO_FILE = DATA_DIR / "nikkes_info.json"
NIKKES_NICKNAME_FILE = DATA_DIR / "nikkes_nickname.json"
FUNCTION_NAMES_FILE = DATA_DIR / "function_names.json"
NIKKE_CHECKLIST_NAME_CODES_FILE = DATA_DIR / "nikke_checklist_name_codes.json"
UNION_RAID_TEMP_FILE = Path(tempfile.gettempdir()) / "BlablalinkTool" / "UnionRaid" / "blablalink_union_raid.json"
GOOGLE_SHEETS_ID_FILE = SETTINGS_DIR / "google_sheets_id.json"
UNION_ACCOUNT_MAP_FILE = SETTINGS_DIR / "union_account_map.json"
CHAR_DETAILS_DIR = Path(tempfile.gettempdir()) / "BlablalinkTool" / "CharacterDetails"

# Google Sheets / Drive
GOOGLE_CRED_FILE = SETTINGS_DIR / "service_account.json"
GOOGLE_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
UNION_RAID_TYPE = "UnionRaid"
CHAR_DETAILS_TYPE = "CharacterDetails"

# XOR key（僅作簡單混淆）
PASS_KEY = "blablalink-secret"
