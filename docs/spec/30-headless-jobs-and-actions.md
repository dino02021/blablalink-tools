# Headless 工作與 Actions

## 工作層

`lib/jobs/` 是 GUI、CLI 與自動化的共用執行層。

目標：
- 將 GUI 能使用的流程轉換為可重複利用的 headless 工作
- 回傳穩定的 `JobResult` 物件
- 保持路由與失敗規則存在於 Python，而非 shell 腳本
- data 類靜態檔案必須直接從 `BlaBlaLinkTool-data` 讀寫，不使用本地 `data/` 目錄作為來源

共用基礎：
- [lib/jobs/common.py](../../lib/jobs/common.py)

## CLI 入口

CLI 入口點：
- [lib/cli/run_job.py](../../lib/cli/run_job.py)

目前命令：
- `union-raid-download`
- `union-raid-upload`
- `union-raid-pipeline`
- `nikke-batch-download`
- `nikke-pipeline`
- `nikke-upload`
- `sync-upload`

## 聯盟突襲工作

### `run_union_raid_download`

檔案：
- [lib/jobs/union_raid_download.py](../../lib/jobs/union_raid_download.py)

流程：
1. 解析帳號
2. 載入會話
3. 呼叫 `fetch_union_info()`
4. 呼叫 `fetch_union_raid_data()`
5. 寫入聯盟突襲 JSON

### `run_union_raid_upload`

檔案：
- [lib/jobs/union_raid_upload.py](../../lib/jobs/union_raid_upload.py)

流程：
1. 載入聯盟突襲 JSON
2. 提取 `union_id`
3. 解析 `type=UnionRaid + union_id`
4. 取得聯盟成員
5. 寫入 Google Sheets

### `run_union_raid_pipeline`

檔案：
- [lib/jobs/union_raid_pipeline.py](../../lib/jobs/union_raid_pipeline.py)

參數：
- `mode=download|full`
- `scope=single|all`
- `month`

規則：
- `single` 執行單一帳號
- `all` 讀取 `BLABLALINK_UNION_ACCOUNT_MAP_JSON`，再降級到本地 `union_account_map.json`
- `full` 表示先下載後上傳
- 此工作流用於手動分派與一次性外部排程

## Nikke 工作

### `run_nikke_batch_download`

檔案：
- [lib/jobs/nikke_batch_download.py](../../lib/jobs/nikke_batch_download.py)

流程：
1. 準備檢查清單項目
2. 從會話上下文讀取 `guild_id` 與 `nikke_area_id`
3. 取得聯盟成員
4. 為每個成員取得角色與角色詳情
5. 寫入 `CharacterDetails/*.json`

### `run_nikke_upload`

檔案：
- [lib/jobs/nikke_upload.py](../../lib/jobs/nikke_upload.py)

規則：
- 依 `type=CharacterDetails + union_id` 路由
- 允許選定 `files`
- 找不到對應試算表時失敗

### `run_nikke_pipeline`

檔案：
- [lib/jobs/nikke_pipeline.py](../../lib/jobs/nikke_pipeline.py)

參數：
- `scope=single|all`

規則：
- `all` 讀取 `BLABLALINK_UNION_ACCOUNT_MAP_JSON`，再降級到本地 `union_account_map.json`
- 依 `username` 去重複
- 跳過沒有對應 `CharacterDetails` 試算表的帳號
- 若任何帳號確實失敗則整個管線失敗

## 同步上傳

檔案：
- [lib/jobs/sync_upload.py](../../lib/jobs/sync_upload.py)

參數：
- `scope=single|all`

規則：
- 依 `guild_id -> CharacterDetails` 路由
- `all` 讀取 `BLABLALINK_UNION_ACCOUNT_MAP_JSON`，再降級到本地 `union_account_map.json`
- 依 `username` 去重複

## GitHub 工作流角色

工作流檔案為執行封裝，支援：
- 從 GitHub 手動分派
- 來自外部排程器的分派請求

它們不應是生產排程時間的來源。

目前工作流檔案：
- [.github/workflows/union_raid_pipeline.yml](../../.github/workflows/union_raid_pipeline.yml)
- [.github/workflows/nikke_pipeline_run.yml](../../.github/workflows/nikke_pipeline_run.yml)
- [.github/workflows/sync_upload.yml](../../.github/workflows/sync_upload.yml)

目前觸發模型：
- `union_raid_pipeline`：僅限手動分派
- `nikke_pipeline`：手動分派加外部 `workflow_dispatch`
- `sync_upload`：手動分派加外部 `workflow_dispatch`

每個工作流現在接受選用的排程器後設資料輸入：
- `scheduler_source`
- `scheduler_job_name`
- `scheduler_fire_time`

這些輸入僅供可觀測性使用，不會改變業務邏輯。

## 外部雲端排程器

生產排程由下列處理：
- [cloudflare-scheduler/](../../cloudflare-scheduler/)

元件：
- [cloudflare-scheduler/wrangler.toml](../../cloudflare-scheduler/wrangler.toml)
- [cloudflare-scheduler/src/index.js](../../cloudflare-scheduler/src/index.js)
- [.github/runtime/scheduler_config.json](../../.github/runtime/scheduler_config.json)

設計：
- Cloudflare Cron Trigger 每分鐘執行一次
- Worker 從 GitHub 儲存庫載入排程器配置
- Worker 以設定的時區評估應執行的工作
- Worker 使用 `workflow_dispatch` 分派 GitHub 工作流

存在原因：
- GitHub 原生 `schedule` 在此儲存庫上證實不可靠
- 排程必須能從 GitHub 或本地 git 編輯
- `union_raid_pipeline` 需要事件日期確定後的一次性事件型排程

## 外部排程器規則

- `scheduler_config.json` 是排程的真實來源
- 不要在工作流 YAML 中硬編碼生產排程時間
- worker 必須保持配置驅動
- 工作流檔案名稱是排程器使用的穩定識別符
- 一次性聯盟突襲工作應表示為 `type=once` 或 `type=interval` 項目
- `type=interval` 必須解讀為從 `start_date + time` 開始的 24 小時區間，終點為隔天同一時間且包含終點

## 工作流開發規則

改變工作流時：
- 保持 shell 邏輯精簡
- 將路由與驗證移至 Python 或 worker 程式碼
- 保持工作流輸入與工作參數一致
- 工作流行為、排程器契約或分派語義改變時更新規格

## 建議的改變順序

改變順序：
1. `lib/jobs/*`
2. 工作流輸入
3. 雲端排程器邏輯
4. 排程器配置契約
5. 規格
## Healthcheck Sheet Layout Notes

- `練度標準` 新角色模板固定為 11 欄。
- 欄寬定義使用 1-based 欄位順序：
- 第 1-7 欄 = `30`
- 第 8 欄 = `47`
- 第 9-11 欄 = `50`
- 實作時必須以 Google Sheets API 的 half-open index 對應這個規格，不能少套第 7 欄或漏掉第 11 欄。
- `練度標準` 同步時不只新插入區塊要套欄寬；既有 11 欄角色區塊也必須重新校正欄寬，避免歷史錯誤殘留。
