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
1. 讀取共用試算表的 `健檢名單`，建立一次設定快照與角色檢查清單
2. 從該快照建立一次 `function_id_targets`，同一批次下載反覆使用，不可每個成員重讀設定或詞條字典
3. 從會話上下文讀取 `guild_id` 與 `nikke_area_id`
4. 解析目標試算表並核對角色與詞條表頭後，取得聯盟成員
5. 為每個成員取得角色與角色詳情
6. 寫入 `CharacterDetails/*.json`，包含角色 `name_code` 及 `healthcheck_context` 來源/目標表頭指紋

詞條規則：
- `function_id_targets` 由共用 `健檢名單` 的詞條1/2/3 依序比對 `function_names.json.function_name`
- 套用詞條正規化後必須完整且唯一匹配，不接受部分文字
- 角色詳情輸出的 `functions` 必須維持 `function1/2/3` 順序
- 取出的詞條數值若為負數，必須先取絕對值再加總與輸出

### `run_nikke_upload`

檔案：
- [lib/jobs/nikke_upload.py](../../lib/jobs/nikke_upload.py)

規則：
- 依 `type=CharacterDetails + union_id` 路由
- 允許選定 `files`
- 找不到對應試算表時失敗
- 上傳角色練度時，詞條欄位使用 `CharacterDetails/*.json` 中 `functions` 的既有順序，不重新排序
- 首次寫入前再次核對來源名單、目標表頭、JSON 來源指紋、角色代碼及詞條 ID；任一不符停止整批寫入
- 舊檔未包含 `healthcheck_context` 時，提示重新下載，不猜測舊詞條設定

### `run_nikke_pipeline`

檔案：
- [lib/jobs/nikke_pipeline.py](../../lib/jobs/nikke_pipeline.py)

參數：
- `scope=single|all`

規則：
- `all` 讀取 `BLABLALINK_UNION_ACCOUNT_MAP_JSON`，再降級到本地 `union_account_map.json`
- 依 `username` 去重複
- 執行 pipeline 時先建立一次 `HealthcheckSnapshot`，再傳給每個帳號的下載與上傳；GUI 單一及全部模式採相同規則
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

### 同步等級的成員移除與資料對齊

- 取得最新聯盟名單後，沿用 `member_id`（Blabla ID）優先、名稱備援的比對規則；任一符合即保留，兩者皆找不到才移除。同 ID 改名及同名換 ID 均沿用既有資料。
- 成員名稱、等級資料不完整或最新名單含重複非空 ID 時停止同步；不可把正規化時略過的成員當作已離開。未取得成員資料時也不可清空舊名單。
- 寫入前必須讀完 `成員名稱`、五屬性練度表及變化表。任一來源讀取失敗時，不得先改主名單或擴充格線。
- 生產同步固定處理 `角色練度_電擊`、`角色練度_水冷`、`角色練度_燃燒`、`角色練度_風壓`、`角色練度_鐵甲`、`角色練度_變化`；缺表時停止，不包含相同前綴的備份表。
- 資料起始列分別為：`成員名稱` 第 2 列、五屬性練度表第 3 列、`角色練度_變化` 第 4 列。表頭不得搬動。
- 保留成員依原主名單順序排列，完整資料列一起往上補。Discord ID、討論串 ID、其他人工欄位，以及練度與變化資料、公式、格式和註記跟隨原成員；同名成員優先依原成員列位移區分。各表順序不同時依既有名稱尋找原資料，不得直接套用主表列號。
- 主名單可能已改名而練度表仍留有更早的舊名。依 ID 判定保留的成員，在名稱找不到時須保留其原成員列對應的練度資料（套用各表起始列差）；不可當成缺列插入空白並清除原資料。先保留其他已明確對應的來源列；來源互相衝突或仍有無法辨識的原資料時，整批停止，不猜測或清空。
- 同步只使用既有實體列，禁止 `insertDimension`、`appendDimension` 或其他擴表操作。新人使用移除舊成員後的空位或既有空白列；任一表容量不足時，在所有寫入前停止並回報，不能自動增加列數。
- 七張表先搬移保留成員並清空尾端舊資料與註記，再更新等級及把新人接在保留成員末端。新人的 Discord、人工欄、練度與變化資料維持空白，不沿用離開者資料。
- 整個搬移、清除與新增流程使用一次試算表層級的 `batchUpdate`，讓七表一起套用，避免單表失敗後留下不同步的名單。請求依序執行及原子套用語義依 [Google Sheets API 批次契約](https://developers.google.com/workspace/sheets/api/guides/batch)。
- 重跑同步應保留已對齊成員的資料與順序，不因 API 回傳順序不同再次洗牌。

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

- Python 健檢名單編輯直接更新共用試算表；原名單上傳 job 改成唯讀的欄位同步檢查。
- Python headless job 不呼叫 gs web app route，也不直接補欄、刪欄或同步 `練度評分結果` 暱稱。
- `角色練度_{element}`、`角色練度_變化`、`練度標準`、`練度評分結果` 的欄位新增、欄寬、外框、底色、合併儲存格、插入位置、刪除欄位與暱稱同步，全部交由試算表內的嚴格同步模式處理。
- 使用者仍需在試算表內確認嚴格同步預覽清單後，才會修改其他工作表的角色欄位；Python 欄位核對失敗時只提示，不自動增刪角色欄位。
