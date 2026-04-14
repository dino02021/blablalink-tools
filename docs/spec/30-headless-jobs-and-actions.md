# Headless Jobs And Actions

## Job Layer 原則

`lib/jobs/` 是這個專案的 headless 核心層。

目的：

- 把 GUI 可用的功能轉成 CLI / Actions 可重用的 job
- 將輸出統一成 `JobResult`
- 讓 workflow 不需要了解 GUI 細節

[lib/jobs/common.py](C:/Users/Yabi/Documents/BlaBlaLinkTool/lib/jobs/common.py) 定義：

- `JobResult`
- `run_coro_sync()`
- `resolve_account()`

## CLI

[lib/cli/run_job.py](C:/Users/Yabi/Documents/BlaBlaLinkTool/lib/cli/run_job.py) 是所有 headless workflow 的統一入口。

啟動順序：

1. `pull_cloud_data_files()`
2. 執行指定 job
3. `flush_cloud_data_sync()`
4. 以純文字或 JSON 輸出結果

這代表 headless job 執行前一定會先同步 repo data。

## Union Raid 流程

### 下載

[lib/jobs/union_raid_download.py](C:/Users/Yabi/Documents/BlaBlaLinkTool/lib/jobs/union_raid_download.py)

流程：

1. 解析 account
2. 讀取 `sessions.json`
3. 呼叫 `fetch_union_info()`
4. 呼叫 `fetch_union_raid_data()`
5. 寫出帶 timestamp 的 union raid 暫存 JSON

輸出重點：

- `outputs.data_path`
- `outputs.created_new_file`

### 上傳

[lib/jobs/union_raid_upload.py](C:/Users/Yabi/Documents/BlaBlaLinkTool/lib/jobs/union_raid_upload.py)

流程：

1. 讀取指定或最新的 union raid JSON
2. 取出 `union_id`
3. 用 `union_id` 路由到 `type=UnionRaid` 的 sheet
4. 用同帳號 session 呼叫 `fetch_guild_members()`
5. 呼叫 GUI 模組中的 `_upload_to_sheets`

行為特點：

- 若未指定 `month`，可透過 `resolve_latest_union_upload_month()` 自動找最新月份
- `BLABLALINK_STRICT_UNION_WEBAPP=1` 時，WebApp 通知失敗會讓整個 job fail

### Pipeline

[lib/jobs/union_raid_pipeline.py](C:/Users/Yabi/Documents/BlaBlaLinkTool/lib/jobs/union_raid_pipeline.py)

支援：

- `mode=download|full`
- `scope=single|all`

`all` 模式來源：

- 優先讀 `BLABLALINK_UNION_ACCOUNT_MAP_JSON`
- 失敗時 fallback 到本機 `union_account_map.json`

## Nikke 流程

### 本帳號下載

GUI 的 [lib/gui/nikke_download.py](C:/Users/Yabi/Documents/BlaBlaLinkTool/lib/gui/nikke_download.py) 直接呼叫 API：

- `fetch_user_characters()`
- `fetch_user_character_details()`

這是桌面專用的單帳號下載。

### 全聯盟批次下載

[lib/jobs/nikke_batch_download.py](C:/Users/Yabi/Documents/BlaBlaLinkTool/lib/jobs/nikke_batch_download.py)

流程：

1. `prepare_checklist_items()`
2. 透過 session 呼叫 `fetch_union_info()`
3. 呼叫 `fetch_guild_members()`
4. 對每位成員：
   - `fetch_user_characters()`
   - checklist 過濾
   - `fetch_user_character_details()`
5. 寫出 `CharacterDetails/<nickname>_character_details.json`

輸出重點：

- `guild_id`
- `char_details_dir`
- `files`
- `ok_count / total_count`

### Nikke 上傳

[lib/jobs/nikke_upload.py](C:/Users/Yabi/Documents/BlaBlaLinkTool/lib/jobs/nikke_upload.py)

路由規則：

1. 用 account 的 session 呼叫 `fetch_union_info()`
2. 取出當前 `union_id`
3. 在 `google_sheets_id.json` 中找 `type=CharacterDetails` 且 `union_id` 精準匹配
4. 將指定或整個 `CHAR_DETAILS_DIR` 的 JSON 寫入對應試算表

實作特點：

- 支援 `files` 過濾，只上傳本次下載成功的檔案
- 支援 change sheet `角色練度_變化`
- 上傳後可觸發 WebApp notify

### Nikke pipeline

[lib/jobs/nikke_pipeline.py](C:/Users/Yabi/Documents/BlaBlaLinkTool/lib/jobs/nikke_pipeline.py)

流程：

1. `run_nikke_batch_download()`
2. 取回 `outputs.files`
3. `run_nikke_upload(files=...)`

這是目前 `nikke_pipeline` workflow 的核心執行單元。

## Sync Upload

[lib/jobs/sync_upload.py](C:/Users/Yabi/Documents/BlaBlaLinkTool/lib/jobs/sync_upload.py)

流程：

1. 用 account 的 session 呼叫 `fetch_guild_members()`
2. 取得成員 `nickname -> synchro_level`
3. 透過 `resolve_character_sheet_target(guild_id)` 找到 `CharacterDetails` sheet
4. 寫入指定 worksheet，預設名稱為 `同步等級`

## GitHub Actions

### `union_raid_pipeline`

檔案：

- [.github/workflows/union_raid_pipeline.yml](C:/Users/Yabi/Documents/BlaBlaLinkTool/.github/workflows/union_raid_pipeline.yml)

使用：

- `sessions` secret
- `union_account_map` secret
- `service_account` secret
- repo runtime `union_google_sheets_id.json`

### `nikke_pipeline`

檔案：

- [.github/workflows/nikke_pipeline_run.yml](C:/Users/Yabi/Documents/BlaBlaLinkTool/.github/workflows/nikke_pipeline_run.yml)

使用：

- `sessions` secret
- `service_account` secret
- repo runtime `nikke_google_sheets_id.json`

目前已不依賴 `BLABLALINK_SHEETS_JSON`。

### `sync_upload`

檔案：

- [.github/workflows/sync_upload.yml](C:/Users/Yabi/Documents/BlaBlaLinkTool/.github/workflows/sync_upload.yml)

使用：

- `sessions` secret
- `service_account` secret
- repo runtime `nikke_google_sheets_id.json`

## Workflow 必要 secrets

目前有效的 workflow secrets：

- `BLABLALINK_SESSIONS_JSON`
- `BLABLALINK_GOOGLE_CREDENTIALS_JSON`
- `BLABLALINK_UNION_ACCOUNT_MAP_JSON`
- `BLABLALINK_DEFAULT_ACCOUNT`
- `BLABLALINK_CLOUD_DATA_TOKEN`

## 目前的重要 routing 規則

- `UnionRaid` 上傳：看 union raid JSON 內的 `union_id`
- `Nikke upload`：看當前 account 對應 session 查出的 `union_id`
- `Sync upload`：看 `fetch_guild_members()` 回傳的 `guild_id`

三者都依賴 `union_id/guild_id`，而不是 sheet name。

## 維護注意事項

- 改 `google_sheets_id.json` 本機設定時，若 workflow 也要吃到同樣 mapping，必須同步更新 `.github/runtime/*.json`
- `nikke_upload` 的部分 helper 目前仍放在 GUI 模組中，不能隨意搬動函式名稱
- workflow 若在 job 建立前就 `startup_failure`，優先檢查是否又引入大型或不必要的 secret restore 步驟
