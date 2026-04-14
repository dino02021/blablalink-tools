# Runtime And Data

## 路徑分層

[lib/constants.py](../../lib/constants.py) 將資料切成三類：

- repo 內靜態資料
  `data/`
- 本機設定 / secrets / sessions
  `$HOME/Documents/BlablalinkToolSettings`
- 暫存輸出
  `%TEMP%/BlablalinkTool/...`

## 主要檔案

### Repo data

- `data/nikkes_info.json`
- `data/nikkes_nickname.json`
- `data/function_names.json`
- `data/nikke_checklist_name_codes.json`

這些檔案是工具執行 Nikke 相關流程時的 lookup data，會被 GUI 與 headless job 共用。

### 本機 runtime 檔案

- `accounts.json`
  本機帳密清單；密碼以 XOR + Base64 儲存。
- `sessions.json`
  各帳號 Playwright `storage_state` 的 cookie 集合。
- `service_account.json`
  Google Sheets service account credentials。
- `google_sheets_id.json`
  試算表設定。由 type 分組，每筆 sheet 可帶 `union_id`。
- `union_account_map.json`
  `union_id -> username` 映射，用於多聯盟 pipeline。

### 暫存輸出

- `UnionRaid/blablalink_union_raid_<union_id>_<timestamp>.json`
- `CharacterDetails/<nickname>_character_details.json`

這些檔案是下載結果，供後續上傳流程使用。

## 設定檔格式

### `google_sheets_id.json`

由 [lib/data_store.py](../../lib/data_store.py) 讀寫，格式為：

```json
[
  {
    "type": "UnionRaid",
    "sheets": [
      {
        "sheet_name": "月下",
        "sheet_id": "...",
        "union_id": "3097"
      }
    ]
  },
  {
    "type": "CharacterDetails",
    "sheets": [
      {
        "sheet_name": "月下",
        "sheet_id": "...",
        "union_id": "3097"
      }
    ]
  }
]
```

路由規則：

- `UnionRaid` 上傳以 `type=UnionRaid` 且 `union_id` 精準匹配為主
- `CharacterDetails` / `sync_upload` 以 `type=CharacterDetails` 且 `union_id` 精準匹配為主

### `union_account_map.json`

格式：

```json
[
  {
    "union_id": "5010",
    "username": "account_a@gmail.com"
  },
  {
    "union_id": "3097",
    "username": "account_b@gmail.com"
  }
]
```

用途：

- `union_raid_pipeline --scope all` 會對每個唯一 `username` 跑一次流程
- 上傳階段仍以下載結果的 `union_id` 決定最終 sheet routing

## Data Store 責任

[lib/data_store.py](../../lib/data_store.py) 負責：

- 讀取與寫入 `google_sheets_id.json`
- 讀取與寫入 `union_account_map.json`
- 將 `nikke_checklist_name_codes.json` 與 `nikkes_info.json` 整理成 `checklist_items`

其中 `save_sheets()` 與 `save_union_account_map()` 會透過 [lib/github_secrets_sync.py](../../lib/github_secrets_sync.py) 同步敏感 runtime 檔到 GitHub secrets。

## Cloud Data Sync

[lib/github_data_sync.py](../../lib/github_data_sync.py) 管理 repo data 與獨立 cloud data repo 的同步。

### 同步對象

- `nikkes_info.json`
- `nikkes_nickname.json`
- `function_names.json`
- `nikke_checklist_name_codes.json`

### 行為

- 啟動時 `pull_cloud_data_files()` 先從 cloud data repo 拉最新版本
- 本地 data 檔異動後，watcher 會自動排程 push 回 cloud data repo
- CLI job 執行完會呼叫 `flush_cloud_data_sync()` 等待 pending push 完成

### 相關環境變數

- `BLABLALINK_CLOUD_DATA_REPO`
- `BLABLALINK_CLOUD_DATA_BRANCH`
- `BLABLALINK_CLOUD_DATA_TOKEN`
- `BLABLALINK_CLOUD_DATA_ENABLED`

## GitHub Secret Sync

[lib/github_secrets_sync.py](../../lib/github_secrets_sync.py) 現在只同步三種 runtime 檔：

- `sessions.json -> BLABLALINK_SESSIONS_JSON`
- `service_account.json -> BLABLALINK_GOOGLE_CREDENTIALS_JSON`
- `union_account_map.json -> BLABLALINK_UNION_ACCOUNT_MAP_JSON`

`google_sheets_id.json` 已不再同步到 secrets，而是改走 repo 內 `.github/runtime/*.json`。

## `.github/runtime/` 的角色

目前 workflow 用到兩份 runtime mapping：

- `.github/runtime/union_google_sheets_id.json`
- `.github/runtime/nikke_google_sheets_id.json`

用途：

- 讓 workflow 不依賴大型或容易出問題的 sheet mapping secret
- 把不同流程所需的試算表映射固定在 repo 中

## 風險與限制

- `sessions.json` 與 `service_account.json` 仍屬敏感資料，不能放進 repo。
- `google_sheets_id.json` 的本機編輯與 workflow runtime 檔目前是兩套來源；若 mapping 變更，需同步調整 `.github/runtime/`。
- Character details 暫存檔以 `nickname` 作檔名；若同聯盟出現重名，後寫入者會覆蓋前者。
