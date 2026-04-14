# Headless Jobs And Actions

## Job Layer

`lib/jobs/` 是 headless 執行的核心層。

目的：
- 把 GUI 可用流程收斂成可直接由 CLI 與 workflow 執行的 job
- 穩定回傳 `JobResult`
- 把 routing、資料驗證與主要失敗訊息集中

共用入口：
- [lib/jobs/common.py](../../lib/jobs/common.py)

重要內容：
- `JobResult`
- `run_coro_sync()`
- `resolve_account()`

## CLI Entry

CLI 入口在：
- [lib/cli/run_job.py](../../lib/cli/run_job.py)

CLI 的責任：
- 解析子命令與參數
- 執行 job
- 統一輸出 `JobResult`
- 在流程前後包住 cloud data sync hook

目前有效子命令：
- `union-raid-download`
- `union-raid-upload`
- `union-raid-pipeline`
- `nikke-batch-download`
- `nikke-pipeline`
- `nikke-upload`
- `sync-upload`

## Union Raid Jobs

### `run_union_raid_download`

檔案：
- [lib/jobs/union_raid_download.py](../../lib/jobs/union_raid_download.py)

流程：
1. 解析 account
2. 載入 session
3. 呼叫 `fetch_union_info()`
4. 呼叫 `fetch_union_raid_data()`
5. 產生 union raid JSON

關鍵輸出：
- `outputs.data_path`
- `outputs.created_new_file`

### `run_union_raid_upload`

檔案：
- [lib/jobs/union_raid_upload.py](../../lib/jobs/union_raid_upload.py)

流程：
1. 讀取 union raid JSON
2. 取出 `union_id`
3. 以 `type=UnionRaid + union_id` 精準匹配 sheet
4. 取得聯盟成員資料
5. 寫入 Google Sheets

規則：
- `month` 可顯式指定。
- 未指定時可由現有資料推導最新月份。

### `run_union_raid_pipeline`

檔案：
- [lib/jobs/union_raid_pipeline.py](../../lib/jobs/union_raid_pipeline.py)

支援參數：
- `mode=download|full`
- `scope=single|all`

規則：
- `single` 模式只跑一個 account。
- `all` 模式讀 `BLABLALINK_UNION_ACCOUNT_MAP_JSON`，失敗時 fallback 到本地 `union_account_map.json`。
- `full` 表示 download 後接 upload。
- 每個 account 獨立記錄結果，最後回傳 summary。

## Nikke Jobs

### `run_nikke_batch_download`

檔案：
- [lib/jobs/nikke_batch_download.py](../../lib/jobs/nikke_batch_download.py)

流程：
1. `prepare_checklist_items()`
2. 由 session 取得 `guild_id` 與 `nikke_area_id`
3. 呼叫 `fetch_guild_members()`
4. 對每位 member 呼叫：
   - `fetch_user_characters()`
   - checklist 過濾
   - `fetch_user_character_details()`
5. 產生本次成功的 `CharacterDetails/*.json`

關鍵輸出：
- `outputs.guild_id`
- `outputs.char_details_dir`
- `outputs.files`
- `stats.ok_count`
- `stats.total_count`

### `run_nikke_upload`

檔案：
- [lib/jobs/nikke_upload.py](../../lib/jobs/nikke_upload.py)

流程：
1. 由 account 對應 session 取得 `union_id`
2. 以 `type=CharacterDetails + union_id` 精準匹配 sheet
3. 讀取指定的角色細節 JSON
4. 寫入主 sheet 與 change sheet

規則：
- 可接受 `files`，只上傳指定檔案。
- `files` 為空時，預設掃整個 `CHAR_DETAILS_DIR`。
- 找不到對應 sheet 時直接失敗。

### `run_nikke_pipeline`

檔案：
- [lib/jobs/nikke_pipeline.py](../../lib/jobs/nikke_pipeline.py)

支援參數：
- `scope=single|all`

流程：
1. 解析要執行的帳號集合
2. 每個帳號先跑 `run_nikke_batch_download()`
3. 只取本次下載成功的 `files`
4. 若該帳號可解析對應 `CharacterDetails` sheet，接著跑 `run_nikke_upload()`

規則：
- `all` 模式讀 `BLABLALINK_UNION_ACCOUNT_MAP_JSON`，失敗時 fallback 到本地 `union_account_map.json`
- 對 `username` 去重後逐帳號執行
- 若缺少對應 `CharacterDetails` sheet，該帳號記為 `skipped`
- 若 download 或 upload 真正失敗，該帳號記為 failed
- 只要存在 failed，整體 pipeline 回傳失敗

## Sync Upload

檔案：
- [lib/jobs/sync_upload.py](../../lib/jobs/sync_upload.py)

流程：
1. 由 account 的 session 取得聯盟成員資料
2. 整理 `nickname -> synchro_level`
3. 以 `guild_id` 路由到 `CharacterDetails` sheet
4. 寫入指定 worksheet

規則：
- `guild_id` 視同 `union_id`
- worksheet 名稱可指定
- 沒有可匹配資料時直接失敗

## Workflow Role

workflow 的角色只有一個：

把既有 job layer 包裝成可在 GitHub Actions 執行的入口。

它不應該：
- 自己重寫業務邏輯
- 自己維護另一套 routing 規則
- 在 shell script 內做大量資料判斷

## Current Workflows

### Union Raid

- [.github/workflows/union_raid_pipeline.yml](../../.github/workflows/union_raid_pipeline.yml)

對應 job：
- `union-raid-pipeline`

### Nikke

- [.github/workflows/nikke_pipeline_run.yml](../../.github/workflows/nikke_pipeline_run.yml)

對應 job：
- `nikke-pipeline`

### Sync Upload

- [.github/workflows/sync_upload.yml](../../.github/workflows/sync_upload.yml)

對應 job：
- `sync-upload`

## Workflow Development Rules

修改 workflow 時維持以下原則：
- 只 restore 該流程真的需要的 runtime 檔
- 盡量把邏輯放回 Python job，不要堆在 bash
- workflow input 命名要與 job 參數一致
- 不要重新引入大型、重複或不必要的 secret restore 步驟

## Current Important Constraints

- `startup_failure` 類問題通常發生在 workflow 初始化階段，而不是 Python job 本體。
- `nikke_pipeline` 與 `union_raid_pipeline` 都已支援 `single/all`。
- `sync_upload` 目前仍是單帳號流程。
- `nikke` 與 `sync` 都依 `CharacterDetails` routing。

## Recommended Change Strategy

若未來要改流程，優先順序是：
1. 先改 `lib/jobs/*`
2. 再改 CLI 參數
3. 最後再改 workflow 包裝

不要反過來先改 workflow shell 行為。
