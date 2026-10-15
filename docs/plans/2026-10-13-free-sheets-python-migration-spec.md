# 試算表系統整合與 GS 邏輯移轉 Python 規格書

| 文件資訊 | 內容 |
| --- | --- |
| 文件版本 | 0.2 |
| 狀態 | 已授權本地 mock 實作；正式整合與部署未授權 |
| 建立日期 | 2026-10-13 |
| 主儲存庫 | blablalink-tools |
| 部署參考 | 本地 nikke-lab |
| 成本目標 | 使用既有免費方案與設備，新增雲端服務支出為 0 |

> 2026-10-15 使用者授權以本規格為 goal 的驗收目標，獨立完成本地開發與 mock 測試。禁止使用正式試算表測試、接上正式服務或部署正式環境。現有正式程式仍以 docs/spec 為準；新系統目前只有本地模組，進度與待辦見第 17 節。

## 1. 需求、範圍與決策

### 1.1 已確認需求

1. 保留 Google Sheets，其他協作者持續在試算表編輯允許的設定。
2. 程式由主要維護者管理，協作者不需要操作 Git 或本機 Python。
3. 最大程度將 GS 的業務邏輯移到 Python，包含評分、健檢、排刀及表格產生。
4. 試算表以設定輸入、協作操作與結果閱讀為主。
5. 整合分散的相關程式與資料契約，降低本機、GitHub、GS 的版本落差。
6. 整體使用免費方案。不得以限期試用、會自動轉付費的資源或付費超額用量作為必要依賴。
7. 現階段進行可獨立完成的本地實作，測試只使用 mock／合成資料。
8. 途中發現的缺漏必須追加到本規格，並維持可追蹤的驗收進度。
9. 可獨立項目全部完成後，列出需使用者介入的依賴，依 goal 工具的狀態規則設為 blocked；不能因尚有本地可開發事項就提前停止。

### 1.2 本文件涵蓋範圍

- blablalink-tools：登入／擷取、GUI、headless jobs、上傳、Actions、Cloudflare 排程。
- nikke-union-management：共用設定、欄位同步、紅字檢查、評分、報告、Discord、排刀及操作入口。
- BlaBlaLinkTool-data：角色與詞條識別字典等相關靜態參照資料。
- nikke-lab：免費部署方式的參考，保留其獨立用途。

本次不把 nikke-lab、其他模擬器或所有名稱含 nikke 的 repository 全部合併。整合對象以這套試算表管理系統的必要程式為準。

### 1.3 主要架構決策

| 編號 | 決策 |
| --- | --- |
| D01 | Google Sheets 是協作者編輯設定的唯一介面；發布後的不可變快照是執行依據。 |
| D02 | 所有遊戲、練度、報告及排刀規則集中在 Python domain。 |
| D03 | GS 保留操作、提示及事件轉送，不執行評分、排刀或欄位重建。 |
| D04 | 使用 Cloudflare Workers Free + D1 Free 接單、保存狀態、排程與協調。 |
| D05 | 通過時間與用量驗證的短任務使用 Vercel Hobby Python；擷取與長任務使用有預算上限的 GitHub Actions。 |
| D06 | 本機 Python 可使用同一任務協定作為執行備援，仍須取得正式發布權限。 |
| D07 | 每個輸出工作簿只有同一套 Python publisher 能正式寫入；跨來源請求走同一協調機制。 |
| D08 | 輸入區與生成區分離，以穩定 ID 建立關係。 |
| D09 | 採單一主 repository、共享 Python 核心、固定發布版本；部署平台可以不同。 |
| D10 | 免費額度不足時保留最後成功結果並等待、降頻或改由本機執行，禁止自動升級付費。 |

## 2. 現況盤點與部署參考

### 2.1 目前試算表系統

| 範圍 | 現有職責 | 目標 |
| --- | --- | --- |
| Python API／下載 | 取得成員、角色與突襲資料，保存投影後 JSON | 保存可重算的原始快照及正規化資料 |
| Python 上傳／同步 | 寫練度、成員與月份出刀資料，通知 GS | 提交資料／任務，透過統一 publisher 產生檢視 |
| healthcheck_sync.gs | 12 欄角色模板、新增、修復、刪除、暱稱同步 | Python layout planner + renderer |
| cf_engine.gs、utils.gs | 等級轉換、門檻比較、紅字與底色還原 | Python evaluator + renderer |
| score_engine.gs | 角色分數、屬性與輔助總分、排名 | Python scoring |
| report.gs | 依紅底背景色產生健檢文字 | Python 依結構化缺項產生報告 |
| discord_publish.gs、GS workflow | 組訊息、dispatch、逐筆發送 | Python report outbox + delivery |
| raid_plan*.gs | 歷史刀、指定刀、候選、模擬、階段、改派與輸出 | Python raid planner |
| triggers_api.gs、central_action_entrypoints.gs、webapp.gs | 事件、公開入口、同步重算、操作狀態 | 中央 GS 薄入口 + 任務 API |
| Cloudflare scheduler | 讀 GitHub JSON 後啟動 workflow | 持久化排程時槽、選擇 executor、追蹤完成 |

現有 Python 已以共用試算表的健檢名單 A:F 作為角色與詞條選擇來源；完整目標設定版本還須涵蓋 G:I、練度標準與穩定 ID。

現況有以下需要在移轉中解決的問題：

- 角色資訊暫存只保留選定三種詞條的加總，改追蹤詞條時可能無法直接重算。
- 健檢報告讀背景色作為缺項事實，計算與呈現耦合。
- 排刀同時讀寫 P:W 指定刀區，人工輸入會隨輸出排序與清空重寫。
- 成員列與角色欄位依名稱、位置對照，跨程式同步時容易錯位。
- 本機、Actions 與 GS 的鎖定範圍不同；通知被接收不等於後續計算完成。
- 路由設定分散在本機檔案、repository runtime JSON 與共用聯盟清單。

### 2.2 nikke-lab 的可沿用方式

本地參考版本為 a135b67。已檢查 README、api/README、vercel.json、api/index.py、api/app.py、web/README、web/package.json 及 CI／Pages 部署 workflow。

| 參考做法 | 本專案採用方式 |
| --- | --- |
| Cloudflare Pages 部署前端 | 初期使用既有 Sheets 介面；未來確有管理頁需求時才新增免費 Pages |
| Vercel 部署 FastAPI／Python | 短任務 executor 與可選預覽 API，沿用無狀態 Python 邊界 |
| CI 執行 Python 與前端驗證 | 建立核心計算、契約及發布前驗證 |
| 前端依 API 取得資料，避免第二套引擎 | GS、GUI、Actions、Vercel 共用同一 Python domain |
| D1 作為可選持久化 | 新系統設立獨立 D1 任務資料庫，不假設 nikke-lab 的正式 DB 已啟用 |
| 原始資料與引擎版本可追蹤 | 每次結果記錄設定、資料、字典、程式及表格結構版本 |

web/README 將正式匿名資料保存描述為 IndexedDB，D1 需另配置；根 README 的 D1 敘述較廣。本規格只確認本地存在 D1 支援，未確認線上 binding 或使用狀態。

nikke-lab 的 HTTP 計算模型不能直接提供持久任務與可靠重試。本專案另外加入 D1 狀態、版本、executor 協調及發布紀錄。

### 2.3 免費條件下的實際調整

2026-10-13 唯讀查詢顯示 blablalink-tools 與 nikke-lab 都是 PRIVATE repository。不得用「公開 repository 的標準 runner 免費」推估本專案沒有 Actions 分鐘限制。

目前本地 scheduler_config.json 含每 3 分鐘啟動聯盟突襲流程的設定。這是本地設定證據，未確認正式 scheduler 是否已使用相同內容。目標架構須加入活動時段、停止條件與用量預算，不能無條件沿用這個頻率。

## 3. 免費架構與執行邊界

### 3.1 目標資料流

~~~mermaid
flowchart TD
    S["Sheets：草稿設定、人工輸入、操作"] --> G["中央 GS：提示與轉送"]
    G --> W["Cloudflare Worker：任務 API／排程"]
    W <--> D["D1：請求、快照、版本、任務、發布紀錄"]
    W --> V["Vercel Hobby：短 Python 任務"]
    W --> A["GitHub Actions：擷取與長 Python 任務"]
    L["本機 Python：登入、擷取／執行備援"] --> W
    V <--> D
    A <--> D
    L <--> D
    V --> P["同一 Python publisher 協定"]
    A --> P
    L --> P
    P --> O["Sheets：受保護的結果、格式、版本與狀態"]
    P --> R["Discord：確認後發送"]
~~~

圖中的 executor／本機存取 D1 均透過已驗證的 Worker API。不得把 D1 帳戶 API Token 暴露給 GS、協作者或一般 client。

### 3.2 Cloudflare Worker

允許職責：

- 驗證服務憑證、操作範圍、request_id、大小限制與允許的命令。
- 保存請求、查詢任務、執行資料庫條件更新與交易。
- 保存 executor／publisher 的 claim、租約、版本及用量紀錄。
- 根據 Python 已產生的任務描述 dispatch 到 Vercel、Actions 或本機。
- 建立固定排程時槽，恢復可重試的計算任務。

禁止職責：

- 判斷角色達標、詞條加總、角色分數、排名、健檢文字或排刀。
- 解析大型原始遊戲 payload 或在 Worker 中進行壓縮、排刀、全量表格渲染。
- 將記憶體 Map、KV 或 workflow concurrency 當成正式鎖與持久佇列。

Workers Free 的 CPU 限制要求此層維持輕量。所有大資料轉換、壓縮與業務驗證由 Python 執行。

### 3.3 Vercel Python executor

- 使用同一套 domain 與 application；套件包不包含 Tk、Playwright、瀏覽器或收集器的非必要依賴。
- 初期執行候選設定驗證、已有快照的重算、報告與小批次發布。
- 排刀僅在代表性資料效能驗證通過後才列為可執行任務。
- 每次 HTTP invocation 的應用時間上限為 240 秒，平台設定以支援的 Hobby 上限為準。
- 單一階段目標 p95 小於 180 秒；遇到剩餘時間不足須保存 checkpoint 並返回。
- 跨 invocation 狀態只能存在 D1，暫存檔只供本次 invocation 使用。
- 不依賴 HTTP 返回後的背景 thread、FastAPI BackgroundTasks 或暫存磁碟完成正式工作。
- Worker 的 scheduled invocation 必須等待 executor 回應；接單 HTTP 返回後的短期 waitUntil 只可做輕量通知，不能承載整套計算。
- executor URL、served_code_sha 與 release_id 必須與任務要求相符；不符時拒絕開始並重新路由。

若某任務無法安全切分、超過效能門檻或免費 CPU／記憶體預算，轉至有預算的 Actions 或本機。

### 3.4 Actions Python executor

- 使用標準 Linux runner，執行 Playwright 資料擷取、初期較長排刀、離線驗證及必要部署。
- 每次執行承領一批已存在的任務；沒有工作時不啟動空 workflow。
- 任務輸入只有 dispatch_id／job_id 等識別碼，不包含成員資料、Cookie 或 Google 憑證。
- workflow 啟動後回報 run_id、attempt、程式 SHA；最後回報實際結果。
- dispatch HTTP 成功只標示 dispatched，不得標示 succeeded。
- workflow 設明確 timeout；預設單 run 上限 20 分鐘，未完成工作保存進度後重新排隊。
- 同類擷取按帳號去重，名單同步、練度擷取與後續計算盡量合併流程，減少重複啟動和安裝。
- 不使用 runner 磁碟、Actions cache 或 artifact 作為跨執行的正式資料庫。

### 3.5 本機備援

- 使用與雲端相同版本的 collector、domain、application、publisher。
- 領取同一 D1 任務，接受同一版本與資源鎖檢查。
- 本機登入工具維持需要人工登入／更新授權的能力。
- 免費雲端額度不足時，管理者可以明確啟動本機執行。
- 本機未啟動或斷線時，任務保持待處理；不假設使用者電腦全天開機。

## 4. 免費額度、用量預算與降級

### 4.1 已查核的官方限制

查核日期：2026-10-13。上線前須重新查核平台條件及帳戶剩餘用量；本表不是額度永久不變的承諾。

| 平台 | 官方免費條件／限制 | 本系統內部預算 |
| --- | --- | --- |
| Workers Free | 每日 100,000 requests；CPU 10 ms／invocation；每帳戶 5 個 Cron Triggers | 一個合併 Cron；業務 API 以每日 20,000 requests 作初期目標 |
| D1 Free | 每日讀 5,000,000 rows、寫 100,000 rows；帳戶總儲存 5 GB | 讀寫警戒線先設官方額度的 70%；查詢必須有合適索引 |
| D1 Free 單 DB | 單資料庫 500 MB；Time Travel 7 天 | 資料庫目標 200 MB、300 MB 警戒；避免膨脹到硬上限 |
| Vercel Hobby + Fluid compute | Python invocation 最長 300 秒；2 GB／1 vCPU；每月含 4 CPU-hours、360 GB-hours | invocation 240 秒應用上限；預設每月分配本專案 1 CPU-hour、90 GB-hours |
| Vercel Hobby 用途 | 個人、非商業用途；超限功能可能須等待恢復 | 部署前確認用途符合；禁止自動升級 Pro |
| GitHub Free 私人 repository | 帳戶每月 2,000 分鐘、500 MB artifact storage；分鐘與其他私人專案共享 | 本專案暫定每月 1,000 分鐘上限；實際依同帳戶其他專案用量下調 |
| Sheets API | 讀、寫各每分鐘每專案 300 requests、每使用者每專案 60 requests | 共用 service account 的讀寫各以每分鐘 30 requests 作初期目標 |
| Apps Script 一般帳戶 | 每次執行 6 分鐘；觸發器總執行時間每日 90 分鐘 | 只接單和顯示，不做長計算；觸發器預期數秒內完成 |

本系統的預算是設計值，不是目前帳戶實測剩餘額度。Vercel、GitHub、Cloudflare 的既有其他專案都要納入上線前容量盤點。記憶體等待網路仍可能消耗 provisioned memory 用量。

官方來源：[Workers limits](https://developers.cloudflare.com/workers/platform/limits/)、[D1 pricing](https://developers.cloudflare.com/d1/platform/pricing/)、[D1 limits](https://developers.cloudflare.com/d1/platform/limits/)、[Vercel Functions limits](https://vercel.com/docs/functions/limitations)、[Vercel Hobby](https://vercel.com/docs/plans/hobby)、[GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions)、[Sheets API limits](https://developers.google.com/workspace/sheets/api/limits)、[Apps Script quotas](https://developers.google.com/apps-script/guides/services/quotas)。

### 4.2 成本控制原則

1. 正式資源維持 Free／Hobby。不得使用 paid runner、付費資料庫、付費容器、必需的 R2 或商業監控。
2. 初期使用 workers.dev、vercel.app 與現有試算表；不要求購買網域或新的主機。
3. GitHub 帳戶層須確認可阻止超額支出；應用預算不能取代帳戶的支出限制。
4. Vercel 用量管理須同時計入 CPU、記憶體與其他受限資源，不只監控 invocation 數。
5. 警戒線到達時合併重算、降低非必要頻率；硬預算不足時標記 capacity_blocked。
6. 額度錯誤不得無限重試；依平台恢復時間安排 next_attempt_at，保留相同請求識別碼。
7. 免費方案提供可控成本與可恢復流程，不保證即時執行或全天無中斷。
8. Worker／D1 無法受理時，GS 保留操作請求及相同 request_id，顯示尚未受理；恢復後補送，不將傳送失敗的請求清除。

### 4.3 初始排程建議

- 平時：每日一次合併抓取成員與練度；可依實測預算增加到兩次。
- 使用者修改標準：重用已取得快照，優先透過 Vercel 重算。
- 突襲期間：必須填開始日、結束日、每日活動時段與間隔；初始間隔 15 分鐘。
- reconcile：每 15 分鐘執行短 Python 檢查，處理漏事件與持續存在的操作請求，不啟動空 Actions。
- 活動結束後自動停用突襲擷取。
- 不以每 3 分鐘全天啟動 Actions 作預設。

示例估算，所有分鐘都是待實測假設：

| 工作 | 假設 | 月用量 |
| --- | --- | --- |
| 平時合併擷取 | 30 次 × 8 分鐘 | 240 分鐘 |
| 突襲擷取 | 3 天 × 每天 6 小時 × 每 15 分鐘一次 × 5 分鐘 | 360 分鐘 |
| CI／部署 | 合計估算 | 120 分鐘 |
| 人工重跑 | 預留 | 120 分鐘 |
| 小計 | 尚未加入同帳戶其他專案 | 840 分鐘 |

實測若超出假設，必須下調頻率或使用本機備援。不能將估算當作免費保證。

## 5. 試算表所有權與資料契約

### 5.1 共用設定工作簿

| 表／區域 | 編輯者 | 用途與限制 |
| --- | --- | --- |
| 健檢名單 A:I | 設定協作者 | 名稱、屬性、類型、三詞條、追蹤、暱稱、優先度 |
| 健檢名單角色 ID／系統對照 | 系統 | 保存 name_code；顯示名稱變更不得改變身份 |
| 練度標準第 4 列 | 設定協作者 | 達標門檻 |
| 練度標準第 5 列 | 設定協作者 | 單一角色各項評分係數 |
| 練度標準第 6 列 | 設定協作者 | 角色總分權重 |
| 練度標準表頭與模板 | 系統 | 角色／項目 ID 對照、合併、欄寬與結構 |
| 聯盟清單 | 管理者 | 路由、啟用狀態、各用途工作簿、writer_mode |
| BOSS血量／排刀季設定 | 排刀協作者 | 季別、月份、階段、BOSS 屬性、基準同步與傷害 |
| 指定刀設定 | 指定協作者 | union_id、member_id、刀位、王、限制、指定傷害 |
| 排程設定 | 管理者 | 時區、頻率、時段、結束日與預算 |
| 操作請求／任務狀態／版本摘要 | 操作人及系統，各自限定欄位 | 提交命令、候選版本、變更摘要、結果及錯誤 |

健檢名單新增受保護的角色 ID 欄或系統對照；具體插欄位置在遷移預覽中確認，不能覆蓋既有額外資料。

共用設定的發布單位分為 healthcheck、raid_season、routing、schedule。發布健檢設定不能意外發布尚在編輯中的排刀草稿。

### 5.2 聯盟工作簿

| 表／區域 | 目標所有權 | 規則 |
| --- | --- | --- |
| 成員名單與同步等級 | Python 生成 | 名稱只作顯示；使用穩定 member_id |
| 成員設定 | 協作者輸入 | Discord ID、Thread ID、顯示別名，依 ID 對應 |
| 人工修正 | 協作者輸入 | 成員、角色、項目、值、啟用狀態、理由 |
| 五張角色練度屬性表 | Python 生成 | 保留熟悉的 12 欄角色版面 |
| 角色練度_變化 | Python 生成 | 顯示兩個真實資料快照間的變化 |
| 練度評分結果 | Python 生成 | 分數、總分及競賽排名 |
| 健檢結果 | Python 生成 | 報告草稿、來源版本與時間 |
| 排刀輸入 | 協作者輸入 | 指定刀、禁止刀、傷害覆寫；不隨結果排序 |
| 排刀結果 | Python 生成 | 結果、警告、草稿與確認版本 |
| 系統狀態／欄位對照 | Python 生成 | 輸入版本、資料時間、release、發布狀態 |

若共用表已有指定刀總表，可選擇沿用為統一輸入表，但須先確認其其他用途並加入 union_id、member_id、season_id。現況排刀主要讀各聯盟 P:W，不能假設共用指定刀總表已是正式來源。

### 5.3 月份出刀工作簿

- 既有每成員兩列、傷害／階段及隊伍圖片顯示可以維持。
- 歷史刀由原始擷取資料產生，排刀直接讀 Python 的同一份歷史資料模型。
- 人工補登、修改階段或傷害須寫獨立歷史更正表，保留來源與操作時間。
- 共享月份出刀工作簿須使用 spreadsheet_id 資源鎖，不能只按 union_id 鎖定。
- 既有 IMAGE／隊伍圖片功能納入遷移；若使用外部圖片加工平台，須先確認現行免費配額。圖片不可成為計算成功的必要依賴；免費限制時保留文字與角色參照。

### 5.4 穩定 ID

| 對象 | 正式 key |
| --- | --- |
| 聯盟 | union_id |
| 成員 | area_id + member_id；目前 member_id 可對應 intl_open_id |
| 角色 | name_code，序列化為字串 |
| 養成項目 | 固定 metric_id |
| 詞條 | function_id，序列化為字串 |
| 季別 | season_id |
| 工作簿 | spreadsheet_id |
| 工作表 | sheet_id，名稱只作人類介面 |

metric_id 初始集合：breakthrough、skill_1、skill_2、skill_burst、equip_head、equip_arm、equip_torso、equip_leg、collection、trait_1、trait_2、trait_3。實際詞條身份另外以 function_id 保存，不能把 trait_1 當成永久詞條身份。

### 5.5 人工修正

- 每筆含 union_id、area_id、member_id、name_code、metric_id 或 function_id、value、is_enabled、reason。
- 應用優先序：原始資料 → 正規化 → 已發布人工修正 → 計算。
- 值為 0、空白、停用及「清除此修正」須有不同語意。
- API 更新不得清除修正；修正應可在介面中查看與停用。
- 停用／刪除角色不直接抹除既有快照與修正；以停用和保留歷史處理。

## 6. 設定草稿、候選與發布

### 6.1 版本模型

- 草稿：試算表當前內容，可能正在多人編輯。
- 候選：Python 擷取、驗證並保存的不可變快照，含變更摘要。
- 已發布：經確認且成為執行依據的候選版本。
- 計算中的工作固定讀其指定版本；後續草稿不影響該工作。

每種設定保存 config_type、config_id、base_config_id、content_hash、schema_version、reference_version、captured_at、validated_at、published_at、actor_ref。

內部設定採結構化角色／項目資料；初期可以保留目前橫向標準表，由 Python adapter 轉換。顯示欄位位置不是內部資料身份。

### 6.2 發布流程

1. 編輯者修改標準／名單，薄 GS 標記尚未發布。
2. 點選「檢查並建立發布候選」，建立 prepare_config 任務。
3. Python 批次讀取相關設定，對擷取前後內容指紋做穩定性檢查；不穩定時重讀，持續變動則顯示 input_changing。
4. 保存候選快照並顯示擷取時間、來源指紋、門檻／權重／角色等變更摘要。
5. 操作人確認明確的 candidate_id，提交 publish_config。
6. Python 檢查 base_config_id 仍為目前版本，透過 D1 原子提交切換版本並建立依賴任務。
7. 各聯盟計算與發布，回報各自完成版本。未成功的聯盟仍顯示最後成功版本。

候選擷取發生在步驟 3 的實際讀取時間，不宣稱是步驟 2 點擊瞬間的內容。第二次確認的對象是已保存候選；確認後的新編輯保留在草稿。

若候選後草稿又變動，畫面須同時顯示「將發布候選 X」與「草稿仍有新變更」。若 base_config_id 已改變，回報 configuration_conflict，重新建立候選。

### 6.3 結構變更

- 新增／刪除角色、屬性移動、追蹤欄位改變、模板修復，須產生 layout plan。
- 預覽包含新增、封存、刪除／隱藏、暱稱改動、受影響工作表及保留的人工資料。
- 真正刪除前保存原值與結構快照；初期優先隱藏／封存。
- 未確認的 layout plan 不可寫入正式工作簿。
- 任何 A 欄成員資料或人工輸入區，不能被模板重建意外清空。

### 6.4 漏事件與程式寫入

- 安裝式 onEdit 用於提示與請求轉送；不能當成唯一事件來源。
- 操作請求在試算表保留 request_id、命令、候選 ID 和受理狀態；API 成功確認後才標示已受理。
- Python reconcile 定期檢查未受理請求、設定指紋及目前生成版本。
- reconcile 可以補送相同 request_id、更新過期提示；不能自行發布未確認草稿。
- Python／Sheets API 修改輸入區時，寫入流程須主動建立變更事件。
- 列／欄插入、貼上範圍等變動須透過內容與結構核對處理，不依賴單格 oldValue。

官方說明：程式執行與 API requests 不會觸發安裝式編輯事件；觸發器以建立者帳戶執行。[Installable triggers](https://developers.google.com/apps-script/guides/triggers/installable)

### 6.5 設定驗證與空值

| 資料 | 規則 |
| --- | --- |
| 角色 | 必須唯一對應已釘版本字典的 name_code；未辨識名稱或同 ID 重複列阻擋發布 |
| 屬性／類型 | 使用目前五屬性與輸出／輔助契約；雙屬性例外保存於參照資料並測試 |
| 詞條 | 非空詞條必須對應 function_id；空槽位標示未設定，不捏造數值 |
| 追蹤 | 首階段維持 G 欄只有「追蹤」納入評分的規則；其他值須顯示是否被略過 |
| 門檻 | 可留空；非空須能依現行數值／等級／加減符號規則解析 |
| 單項係數 | 可留空表示略過該項；非空須為有限數值；負數須在預覽中提示 |
| 總分權重 | 小於等於 0 或未設定不參與總分；非空無效數值阻擋發布 |
| 人工修正 | 明確型別與 metric／function 對照；0 是有效值，不等於未設定 |
| 排刀季 | 月份、BOSS、階段及單位完整；基準同步必須大於 0 |
| 路由 | union_id 與各用途 spreadsheet_id 完整；不能把傷害來源當聯盟結果目標 |
| 排程 | 有限時段、明確結束條件、有效頻率及可分配預算 |

現況 GS 對某些無法解析的門檻直接視為通過；目標設定發布改為顯示輸入錯誤，這項行為差異須在遷移比較中列出，不能無聲改變正式結果。

## 7. 原始資料、模型與重算規則

### 7.1 快照

raw_snapshot 保存：

- union_id、area_id、抓取開始／完成時間、來源與來源結果。
- 成員清單及角色擁有資訊，包括突破。
- character_details 原始相關欄位、全部取得的 equip_option、state_effects 及收藏品資料。
- 歷史刀、傷害、階段、隊伍參照及來源月份。
- 取回的角色範圍、資料完整性、缺失與授權狀態。
- source_schema_version、reference_version、snapshot_hash。

Cookie、密碼、session token 與 Google 憑證不屬於 raw_snapshot。

API 只回傳部分角色時，快照須記錄 coverage；不能宣稱已取得所有角色。新角色／詞條不足時建立需要的補抓任務。

### 7.2 正規化資料

~~~json
{
  "schema_version": 1,
  "union_id": "union_demo",
  "area_id": "area_demo",
  "member_id": "member_demo",
  "name_code": "nikke_demo",
  "ownership_status": "owned",
  "metrics": {
    "breakthrough": 3,
    "skill_1": 10,
    "skill_2": 7,
    "skill_burst": 10,
    "collection": "SR15"
  },
  "traits_by_function_id": {
    "function_demo": "12.34"
  },
  "raw_snapshot_id": "snapshot_demo",
  "collected_at": "2026-10-13T04:00:00Z"
}
~~~

以上是假資料格式示例；function_demo 不是正式遊戲 ID。

ownership_status 至少區分 owned、not_owned、unknown。抓取失敗、快照不涵蓋該角色和確認未持有角色不能混為一談。

### 7.3 達標與報告

- Python 回傳每成員、角色、項目的 value、threshold、comparison_rule、evaluation_status。
- evaluation_status 至少區分 passed、failed、not_applicable、unknown。
- 顏色與健檢文字都由這份結果派生。
- 保留已確認的 T9／R／SR 轉換及加減符號比較行為；首階段不自行更改語意。
- 所有缺值、無效門檻與未持有角色行為須用固定案例確認。
- 只有確認未持有角色才產生「請抽取」。unknown 顯示資料不足或保留上一份有效報告。
- 暱稱、優先度與忽略由相同發布設定取得。
- 報告版本固定綁定設定與資料；手動調色不改變報告事實。

### 7.4 評分

- 角色原始分：有效項目值乘第 5 列係數後加總。
- 角色分數：以同聯盟該角色有效成員最高原始分正規化，保留現有整數截斷與上下限規則。
- 屬性／輔助總分：依第 6 列角色權重加總後正規化。
- 競賽排名：同分同名次，下一名跳號，例如 1、1、3。
- 有效成員由已發布 roster／membership 快照決定；中間空白列不截斷。
- 初期每聯盟完整重算相關群組，避免最高分基準變動後其他成員殘留舊分數。
- Python round 與 JavaScript Math.round 的差異須明確處理；保留現況的取整階段，不只比最終大概數值。

### 7.5 變更影響

| 變更 | 重算範圍 | 需不需要重新擷取 |
| --- | --- | --- |
| 門檻 | 達標、顏色、健檢報告 | 有完整快照時不需 |
| 各項評分係數 | 角色分數、總分、排名；標記排刀依據變更 | 不需 |
| 角色總分權重 | 總分、排名；標記排刀依據變更 | 不需 |
| 暱稱／優先度 | 顯示、報告 | 不需 |
| 新角色／換追蹤詞條 | 資料投影、結構、檢查、評分、報告 | 依快照 coverage 判斷 |
| 成員加入／離開 | 成員檢視、最高分基準、總分、排名 | 更新成員與必要角色資料 |
| 人工修正 | 相關檢查、群組評分及報告 | 不需 |
| BOSS／指定刀／歷史刀 | 排刀草稿 | 歷史刀不足時補抓 |

### 7.6 練度變化

- 比較前後兩個實際資料快照，保留兩個 snapshot_id。
- 設定門檻或權重變更本身不應被當成遊戲練度進步。
- 詞條選擇改變時，以穩定 function_id 判斷是否可比；不能把不同詞條相減。
- Python 一併產生變化內容及角色群組隱藏／顯示設定。

## 8. 排刀與發送

### 8.1 排刀輸入

排刀直接接收同版本 Python 模型：成員／同步等級、輸出排名、季別與 BOSS、歷史刀、指定刀、禁止刀、傷害覆寫。不重新從輸出試算表讀取排名作中繼資料。

### 8.2 規則保留

- 每成員最多三刀，扣除歷史與有效指定刀。
- 保留正向／反向指定、同王重複規則及指定刀優先權。
- I～III 有限血量、IV-5 無限尾段；IV 開放條件保留。
- 保留歷史刀不可移動、溢傷不跨階段、候選排序、輪次、改派與缺刀警告。
- 保留燃燒／鐵甲與雙屬性相關例外。
- 同步三次方傷害估算、單位億與真實傷害的轉換須明確。
- 傷害與權重用可控制精度的表示法；跨 JavaScript 傳輸不使大整數／decimal 損失精度。

### 8.3 結果版本

- 輸出 draft_plan_id、input_versions、algorithm_version、assignments、warnings、shortage_summary。
- 操作人確認後形成 confirmed_plan_id。
- 新排名或歷史資料產生新草稿與過期提示；不靜默替換已確認版本。
- 指定刀輸入保留原序與身份；排刀結果排序不重寫輸入。
- 無效指定刀顯示警告，不直接刪掉協作者的原輸入。

### 8.4 Discord outbox

- 產生報告與確認發送是分開的命令。
- 發送固定綁定 report_version、member_id、Discord route_version 及 target thread。
- 保存 outbox_id、content_hash、attempt、response、message_id 與 sent_at。
- 明確未送出的暫時錯誤可重試；HTTP timeout 但可能已送出的情況標記 delivery_uncertain。
- 不承諾新訊息發送 exactly-once；有已知 message_id 時可考慮更新原訊息，未知結果須核對。
- 依官方 rate limit 回應安排重試，避免重複 ping。
- 標準修改或自動重算不直接觸發 Discord 發送。

## 9. 任務、儲存與 API 契約

### 9.1 D1 邏輯表

| 表 | 保存內容與必要索引 |
| --- | --- |
| requests | request_id、命令、scope、提交／受理狀態；request_id 唯一 |
| config_candidates | 不可變候選、base、驗證結果、摘要、內容 hash |
| config_versions | 已發布設定、schema、reference、actor、時間 |
| config_heads | config_type／scope 的目前發布指標與 revision |
| union_registry | 已發布路由投影，不是第二個可獨立編輯的設定來源 |
| data_snapshots | 完整性、角色 coverage、來源、時間、hash、大小 |
| snapshot_chunks | 依 object_id、序號保存壓縮分片；有 hash 與完整性標記 |
| input_versions | 已發布人工修正、指定刀、成員／歷史更正 |
| jobs | 狀態、依賴、executor、lease、版本、attempt、next_attempt_at |
| job_events | 狀態變更與安全摘要，按 job_id／時間查詢 |
| resource_locks | spreadsheet_id 等資源、holder、generation、狀態 |
| result_versions | 計算結果及所依據的完整版本組合 |
| publications | publication_id、commit_id、目標、進度與驗證結果 |
| report_outbox | 固定內容／路由、發送與未知結果 |
| schedule_slots | schedule_id + UTC 時槽唯一；防重複排程 |
| usage_budget | 平台預算、保留量、已用量、暫停／恢復狀態 |
| releases | code SHA、Python／GS／Worker 版本、schema、參照資料版本 |

這是邏輯契約，正式 SQL migration 在實作階段產生。核心條件更新須依 D1 實際交易能力設計與測試；不能把多次 HTTP 的讀改寫視為交易。

### 9.2 快照分片

- 壓縮及 hash 計算由 Python 完成；使用 JSON 等明確格式，禁止不可信 pickle。
- 每片原始二進位目標不超過 32 KiB；控制 API 單次 body 初期限制 64 KiB，超過則分片。
- snapshot manifest 含 chunk_count、順序、codec、未壓縮大小及內容 hash。
- 所有分片完整且驗證成功後才 sealed；未 sealed 物件不能成為計算輸入。
- 刪除按依賴引用與保留政策處理，不掃描所有大 payload。

### 9.3 命令

prepare_config、publish_config、refresh_roster_and_training、refresh_raid_history、publish_union_inputs、recompute_healthcheck、prepare_layout、apply_layout、generate_raid_draft、confirm_raid_plan、send_reports、reconcile。

每個命令均有自己的前置條件和範圍；「重算」不隱含擷取、「產生報告」不隱含發送。

### 9.4 請求示例

~~~json
{
  "request_id": "00000000-0000-4000-8000-000000000001",
  "command": "publish_config",
  "scope": {"config_type": "healthcheck", "scope_id": "common"},
  "candidate_id": "candidate_demo",
  "base_config_id": "config_previous_demo",
  "requested_at": "2026-10-13T04:05:00Z"
}
~~~

受理回應：

~~~json
{
  "request_id": "00000000-0000-4000-8000-000000000001",
  "job_id": "job_demo",
  "state": "queued",
  "status_url": "/v1/jobs/job_demo"
}
~~~

202／queued 只表示已持久化受理。完成狀態必須依 executor 與 publisher 的實際回報更新。

### 9.5 任務必要欄位

- job_id、request_id、command、scope、priority、created_at。
- config_version_ids、data_snapshot_ids、input_version_ids、reference_version、code_sha、sheet_schema_version。
- idempotency_key、parent_job_id、dependency_job_ids。
- state、executor_type、executor_run_id、attempt、checkpoint_ref。
- lease_owner、lease_generation、lease_expires_at、heartbeat_at。
- next_attempt_at、error_code、safe_error_detail、result_version_id、publication_id。

計算的 idempotency_key 包含命令、scope 及全部實際依賴版本。同一組輸入重試可重用結果，不能因 request UUID 不同而永遠重做相同計算。

### 9.6 狀態

主要流程：

~~~text
queued → dispatched → running → computed → publishing → succeeded
~~~

其他狀態：awaiting_confirmation、waiting_for_data、retry_wait、auth_required、capacity_blocked、publish_uncertain、failed、superseded、cancelled。

- 同 scope 尚未執行的自動重算可合併到最新已發布版本。
- 使用者確認過的發送、排刀版本及歷史操作不得任意合併掉。
- 計算租約失效可重新計算；正式發布未知結果不能套用相同自動接管規則。
- 較舊計算完成後，若正式 desired_versions 已改變，結果留歷史並標 superseded，不發布到目前檢視。

### 9.7 API 介面

| API | 用途 |
| --- | --- |
| POST /v1/requests | 持久化接單，依 request_id 去重 |
| GET /v1/jobs/{job_id} | 查詢安全狀態與版本 |
| POST /v1/executors/claim | 領取可執行任務，原子取得租約 |
| POST /v1/jobs/{job_id}/heartbeat | 更新受控進度與租約 |
| POST /v1/objects/{object_id}/chunks | 上傳受限大小的分片 |
| POST /v1/objects/{object_id}/seal | 封存完整物件 manifest |
| POST /v1/jobs/{job_id}/complete-compute | 保存計算結果與待發布描述 |
| POST /v1/publications/claim | 取得目標工作簿的正式發布權限 |
| POST /v1/publications/{id}/complete | 保存發布及讀回驗證結果 |
| POST /internal/execute | Vercel 的已驗證單階段執行入口 |
| GET /health | served_code_sha、release_id、schema 相容範圍 |

具體 HTTP schema 在實作階段固定為可驗證的契約。client 不可任意指定 executor URL、程式 branch、Google credentials 或未登録的工作簿。

## 10. 發布、並行與故障恢復

### 10.1 正式寫入權

- 所有 executor 共享同一套 publisher，寫入前都須經 D1 claim。
- 鎖定 key 以實際 spreadsheet_id 為準；跨工作簿工作依固定排序取得資源。
- 初期全系統同時只允許一個正式 Sheets publisher，計算最多同時兩個。
- GS／GUI／舊 workflow 對已切換工作簿只提交命令，不再直接寫生成區。
- 操作狀態區可以由薄 GS 顯示「提交中／提交失敗」，正式任務狀態由 Python 回寫。
- GS LockService 不能協調無關 Python client，正式協調以中央儲存狀態為準。

### 10.2 發布流程

1. 已完成的結果在 D1 封存，生成 publication manifest。
2. 取得實際輸出工作簿的發布權限，確認 desired_versions、release 與結構 revision。
3. 先生成完整資料與格式請求，不在計算途中逐格改正式結果。
4. 可行時，以同一工作簿單次 batchUpdate 提交值、必要格式與 commit_id／版本標記。
5. 資料較大時先使用系統 staging；不完整 staging 不切換正式版本。
6. 讀回 commit_id 及必要資料範圍驗證，再標示 publication succeeded。
7. 跨工作簿逐一記錄完成與失敗，不宣稱所有聯盟同時原子完成。

batchUpdate 能將同一請求一起套用，但協作者修改仍可能影響最終內容；因此生成區受保護、人工輸入獨立。[Sheets batchUpdate](https://developers.google.com/workspace/sheets/api/reference/rest/v4/spreadsheets/batchUpdate)

### 10.3 租約與未知發布

- lease_generation 防止失去計算租約的 executor 回報有效完成。
- Sheets API 沒有直接接受本系統 fencing token；僅在 D1 檢查租約不能保證已送出的舊 HTTP 寫入會被阻止。
- 發布開始後遇到 timeout／executor 消失，標記 publish_uncertain，保留發布資源阻擋後續寫入。
- 先核對舊 invocation／run 的終止狀態、commit_id 與工作簿內容；確認原發布已完成，才結束紀錄。
- 確認原發布未完成且舊 writer 已停止後，才重試相同 publication 或授予下一個 writer。
- 無法確認時要求管理者核對；不在 TTL 到期後直接讓新 writer 蓋過未知結果。

### 10.4 錯誤分類

| 類型 | 處理 |
| --- | --- |
| 資料不足 | waiting_for_data，保留最後有效結果，排補抓 |
| 設定無效／結構衝突 | 明確錯誤位置，待修正，不自動修改人為輸入 |
| Session 失效 | auth_required，明確更新授權後再重跑 |
| 暫時網路／429 | 有上限的退避重試，保存 checkpoint |
| dispatch 未確認 | 追蹤 dispatch_id／run_id；重複啟動仍由 claim 去重 |
| 免費容量不足 | capacity_blocked，降頻、等待或本機備援 |
| Sheets 發布未知 | publish_uncertain，核對後恢復 |
| Discord 發送未知 | delivery_uncertain，防止盲目重複發送 |
| 輸入版本過期 | superseded，不發布到目前檢視 |

## 11. 安全、保存與可觀測性

### 11.1 憑證與權限

- Google 憑證只存 executor 必要的秘密環境；BlaBla session 僅給 collector。
- 薄 GS 以 Script Properties 保存受限服務憑證，不放入試算表儲存格或 Git。
- Worker 依 principal 限制命令與工作簿；executor、collector、publisher 使用不同操作範圍。
- HMAC 或等效驗證含 timestamp、nonce、body digest、request_id；具去重與重放限制。
- 觸發器的建立者身份不等於編輯者身份；無法取得 actor 時如實保存 unknown，不用虛構 email 授權。
- 發布／發送操作區用範圍保護限制操作人；一般協作者只編輯指定輸入區。
- 公開 URL、CORS 或知道 spreadsheet_id 都不構成授權。
- 本機授權同步須為明確操作，回報遠端是否保存成功；不將背景檔案監控的啟動當同步成功。

### 11.2 保留與備份

- 每聯盟至少保留目前及上一個可比較資料快照；其他原始快照初始保留最近 7 份／30 天，依儲存預算調整。
- 目前正式設定、確認排刀、未完成任務、待核對發送所引用的物件不能直接清除。
- 候選／設定歷史初始保留最近 20 版與 90 天，引用中的版本額外保留。
- 完整結果初始保留最近 10 版，任務安全摘要保留 90 天；清理不影響目前成功版本。
- 半完成且無引用的分片物件可在 24 小時後分批清理。
- D1 Time Travel 作短期恢復；維護者定期下載加密的本機匯出作獨立備份。
- 不使用 Actions artifact 保存正式成員資料或快照；不把遊戲成員資料、憑證或生成結果 commit 到 Git。
- 日誌不輸出密碼、Cookie、token、完整成員 payload 或完整報告。

### 11.3 試算表狀態

至少顯示：草稿是否變更、候選／已發布設定版本、資料取得時間與完整性、生成版本、程式 release、job_id、狀態、最後成功時間、安全錯誤與下一步。

同一共用設定對多聯盟發布時，顯示每個聯盟的完成版本，不只顯示「共用任務成功」。

## 12. Repository、發布與版本協作

### 12.1 目標目錄

~~~text
blablalink-tools/
  domain/                 純 Python 檢查、評分、報告、排刀
  application/            設定發布、任務、依賴與發布流程
  adapters/               BlaBlaLink、Sheets、Discord、狀態 API
  executors/
    vercel/               短任務入口
    actions/              擷取與長任務 CLI
    local/                本機 worker 入口
  coordination/           Worker 任務 API、D1 migrations、Cron
  apps_script/            中央薄 GS 與部署描述
  client/                 GUI／登入／管理工具，逐步承接既有 lib
  contracts/              設定、資料、API、工作表 schema
  reference/              可版本化的角色與詞條字典
  tests/                  合成 fixture、規則、契約、恢復與效能驗證
  docs/spec/              目前已實作規格
  docs/plans/             未實作設計與遷移規劃
~~~

這是目標分層，不要求第一階段一次搬動所有既有 lib。domain 不得 import Tk、gspread、Playwright 或平台特定 runtime。

### 12.2 整合策略

- 以 blablalink-tools 為主 repository，保留原專案歷史與來源說明。
- GS 原碼及契約搬入 apps_script；原 GS repository 切換成封存／參照用途的時間由遷移里程碑決定。
- 靜態角色／詞條字典採固定版本，初期可釘住資料 repository 的 commit，後續視資料生成流程搬入 reference。
- runtime、session、快照與生成資料維持 Git 排除。
- 不把 nikke-lab 的業務引擎、網站、DB 或秘密環境直接共用過來。

### 12.3 發布規則

- 使用 feature branch 與 CI，部署只採用已驗證的確切 SHA。
- 免费私人 repository 不假設有付費 branch protection／environment reviewer；可用 workflow 明確檢查成功 SHA 後部署。
- Vercel 不得由尚未驗證的 main push 直接切換正式版本；改成驗證通過後的受控部署流程。
- release manifest 含 Python SHA、Worker SHA、GS version、schema 相容範圍、reference version。
- 初始 Python 版本候選 3.12；實作前確認所有免費 executor 支援，統一 interpreter 與依賴 lock。
- 測試／staging 工作簿與正式工作簿分開；測試不使用真實 Cookie 或發送正式 Discord。
- 回滾採完整相容 release 組合；schema 變動需提供讀取相容範圍與資料恢復方式。
- 作業設定的變動由試算表發布，不產生 GUI 對目前 Git branch 的遠端 commit。

## 13. 分階段修改範圍

里程碑執行狀態以第 17 節為準。雲端資源與正式環境操作仍未授權。

| 階段 | 修改範圍 | 交付物與完成門檻 |
| --- | --- | --- |
| M0 規格確認 | 本文件、現況／線上版本核對、樣本與容量盤點 | 決策、真實結構及免費帳戶條件確認；不變更正式資料 |
| M1 Python 核心 | evaluator、scoring、report、raw snapshot、固定字典版本 | 離線結果對齊；無正式寫表 |
| M2 任務與短 executor | Worker／D1、Vercel、薄 GS、版本與請求 | 候選→確認→重算→狀態；只使用 staging |
| M3 單聯盟切換 | member ID、輸入分離、publisher、layout、Actions collector | 正式單聯盟完成可回滾切換；舊 writer 停用 |
| M4 排刀與歷史 | raid planner、獨立指定刀、歷史模型、效能、草稿／確認 | 全部代表性排刀案例及未知發布恢復通過 |
| M5 全聯盟整合 | 路由、排程、Discord、資源預算、舊入口清理 | 全聯盟版本可追蹤，免費預算與備援驗證完成 |

### 13.1 舊模組的具體處置

| 現有位置 | 預定修改 |
| --- | --- |
| lib/api.py | 分離原始快照保存與三詞條投影；保留現有登入／授權邊界 |
| lib/healthcheck_config.py | 擴充為發布設定、schema、版本組合的驗證 adapter |
| lib/google_sheets/healthcheck.py | 保留草稿讀取／編輯，執行端改讀指定已發布版本 |
| lib/jobs/nikke_upload.py | 轉為命令／輸出流程包裝，正式寫入集中 publisher |
| lib/jobs/sync_upload.py | 成員身份與練度輸出同一 pipeline，保留人工設定依 ID 對應 |
| lib/jobs/union_raid_upload.py | 將 GUI 依賴抽出，直接接歷史模型與排刀任務 |
| lib/gui/dialogs/nikke_upload.py | 替換 detached 通知，顯示受理／計算／發布狀態 |
| lib/gui/dialogs/union_raid_upload.py | 保留互動介面，業務與上傳移入共用 adapter/application |
| lib/gui/dialogs/scheduler_config.py | 改為設定發布介面，不對目前 code branch 寫 runtime JSON |
| lib/github_secrets_sync.py | 明確同步與回報，避免本機寫入被當作雲端同步成功 |
| .github/workflows/* | 共用 executor、預算、確切 SHA、結果回報與必要 concurrency |
| cloudflare-scheduler | 演進為 coordination，加入 D1 時槽與任務生命週期 |
| GS 計算／渲染模組 | 逐階段以 Python 替換；切換 scope 不得雙邊正式計算寫入 |
| GS 操作入口 | 只提示、建立／確認命令、顯示 request／candidate ID |

### 13.2 單聯盟切換

1. 備份工作簿內容、結構、人工輸入及舊部署資訊。
2. 在 staging 做同快照比對與完整發布。
3. 預覽人工資料拆分，確認 ID 無歧義及設定引用完整。
4. 暫停該 scope 的舊排程、通知及寫入入口，核對沒有舊工作仍執行。
5. 必要時分離舊／新 Google 寫入憑證與生成區權限，防止舊 client 持續直寫。
6. 更新 writer_mode、結構版本、正式 release；發布第一版結果。
7. 驗證草稿、候選、重算、排刀和錯誤回報。
8. 回滾須先停止新 writer、確認沒有未知發布，再恢復舊版及對應資料；不能兩邊同時啟用。

## 14. 驗收與測試矩陣

| 編號 | 情境 | 必須結果 |
| --- | --- | --- |
| A01 | 多人編輯標準後建立候選，再繼續編輯 | 候選固定，後續草稿不被默默納入 |
| A02 | 確認發布門檻 | 對指定版本產生缺項、顏色、報告；不重新抓已有完整資料 |
| A03 | 修改評分係數、最高分成員資料 | 相關所有成員分數、總分、排名一致 |
| A04 | 同分、R／SR／T9、加減門檻、0、空白 | 依已確認規則與精度得到精確結果 |
| A05 | 新角色／換詞條 | coverage 足夠直接重算；不足顯示待補抓 |
| A06 | 中間空白列、重名、改暱稱、離盟 | ID 正確對應，人工設定不錯位 |
| A07 | 相同 request 重送／重複 dispatch | 同一受理紀錄，正式結果不重複發布 |
| A08 | 本機、Vercel、Actions 同時要求寫同工作簿 | 只有取得正式資源權限的 writer 執行 |
| A09 | 共用出刀工作簿被多聯盟使用 | 以實際 spreadsheet_id 串行發布 |
| A10 | 工作執行途中發布新設定 | 舊結果保存歷史，不能覆蓋新 desired_versions |
| A11 | onEdit 漏事件／API 編輯輸入 | reconcile 恢復請求；未確認草稿不自動發布 |
| A12 | Sheets timeout、executor 消失 | publish_uncertain 阻擋後續 writer，核對後恢復 |
| A13 | 所有聯盟中一個失敗 | 保留該聯盟最後成功版本，顯示部分完成並可單獨重試 |
| A14 | Session 失效 | auth_required，不當作未抽角色／練度為 0 |
| A15 | 指定刀、反向指定、歷史同王、多行、尾刀、溢傷 | 固定案例對齊已確認排刀規則 |
| A16 | 排刀結果排序 | 指定刀原輸入與成員身份維持，輸入不被清空 |
| A17 | Discord timeout | 保存未知結果，避免盲目重發與重複 ping |
| A18 | 免费額度達警戒／硬上限 | 降頻或 capacity_blocked；不自動付費 |
| A19 | 本機備援、程式版號不符 | 同協定領取；不相容 executor 拒絕工作 |
| A20 | Vercel 時間／記憶體上限 | 提前保存進度或轉 executor，不依賴背景續跑 |
| A21 | staging 有未完成資料／結構變更被取消 | 正式結果與人工資料維持有效 |
| A22 | 跨版本回滾與備份還原 | 恢復相容 release／設定／資料，沒有雙 writer |

測試層級：

- 純計算單元測試：合成資料，精確比較分數、缺項與排刀。
- 現有 GS 與新 Python 的固定快照比對：每一差異須分類為 bug、已核准規則調整或尚待釐清。
- 契約／整合測試：Worker／D1 條件更新、分片、版本與 API。
- 故障注入：重複 dispatch、超時、429、授權失效、舊 writer、發布未知。
- staging 工作簿驗證：生成內容、版面、合併、顏色、圖片降級與人工區保護。
- 免費容量／效能測量：記錄 p50／p95、CPU、記憶體時間、Actions 分鐘、D1 行／大小、API 次數。

不以「HTTP 200」、「workflow 已啟動」或「有寫入部分儲存格」作整體成功判定。

## 15. 尚待實作前確認的項目

1. 正式 GS deployment、觸發器、所有實際工作簿結構與可編輯範圍。
2. GitHub 帳戶方案、私人 repository 共享剩餘分鐘、阻止超額支出的設定。
3. Vercel Hobby 帳戶用途、Fluid compute、已有 nikke-lab CPU／記憶體消耗及可分配預算。
4. Cloudflare 帳戶既有 Workers／Cron／D1 用量與部署權限。
5. 收集所有角色原始詞條的可用範圍與每次快照實際大小。
6. 單聯盟與所有聯盟擷取耗時、排刀最差情境與 Vercel 適用性。
7. 人工修正、成員設定、指定刀、歷史補登的現行使用方式。
8. 目前圖片加工平台免費額度與可接受降級方式。
9. 欄位刪除採封存／隱藏還是真正刪除，以及恢復期限。
10. 誰能發布共用設定、確認排刀與發送報告；觸發器建立者與長期維護帳戶。

目前允許以本文件預設值實作本地 mock 驗證，包含本地測試資料庫與 fake provider。不得建立雲端資源、部署服務、讀取正式憑證或操作任何正式試算表；外部 staging 也必須等待額外授權。

## 16. 來源與證據範圍

本地證據：

- blablalink-tools 本次盤點之程式碼基準 commit：1e7da50；另有使用者未提交修改，本次規劃不更動其內容。
- docs/spec/10-runtime-and-data.md、30-headless-jobs-and-actions.md、35-apps-script-webapp-contract.md。
- lib/api.py、lib/healthcheck_config.py、lib/google_sheets/healthcheck.py、相關 jobs／GUI 通知模組。
- .github/runtime/scheduler_config.json、.github/workflows 與 cloudflare-scheduler。
- nikke-union-management 的 src/*.gs 與 docs/code-spec-zh，涵蓋表格契約、同步、評分、健檢與排刀。
- nikke-lab 本地基準 a135b67 的部署與 API 文件／設定。
- 2026-10-13 的 repository visibility 唯讀查詢，以及第 4 節所列官方平台文件。

圖索引對 GS 原始碼未提供完整覆蓋，本地 GS 功能依原始碼與契約交叉核對；nikke-lab 圖索引有較舊 generation，部署判斷採目前檔案內容。本文未執行正式 GS、Vercel、Cloudflare 或試算表部署，也未用線上結果證明本地設定已生效。

本文件新增的命令、版本、工作表、API、資料表、租約及預算皆為目標規格；不能引用為目前程式已具備的能力。

## 17. 本地實作進度與追加事項

### 17.1 執行限制

- goal：以本規格定義的功能與驗收為最終依據，優先完成無需使用者參與的項目。
- 新開發在 local/mock-sheets-migration 分支，使用者原有未提交修改不納入本地開發 commit。
- 僅本地 commit，不 push／dispatch／部署，避免觸發正式流程；此限制優先於舊規範中的立即 push。
- local_tests 獨立於既有 tests/conftest.py，避免匯入舊版設定或憑證載入邏輯。
- local_tests 自動封鎖 socket 連線、DNS 查詢及 datagram 發送。所有 fixture 使用合成識別碼與資料。
- domain 無網路、GUI、平台或憑證相依；尚未被既有正式入口匯入。

### 17.2 第一批已完成

- domain/healthcheck.py：等級／數值解析、結構化門檻判斷、全聯盟角色／總分／競賽排名。
- 支援雙屬性輸出、輔助群組、追蹤開關、非正權重、最高分基準與有效成員範圍。
- local_tests/test_healthcheck.py：39 個合成案例通過；包含網路禁止機制本身的測試。
- Python 語法檢查通過。這不代表 M1 的報告、快照、GS 對照及完整驗收已完成。

### 17.3 本次發現並追加的細節

- 舊 GS 對屬性／輔助加權原始總分先 Math.round，再依最高總分正規化並向下取整。Python 不能直接採用 round 的偶數捨入。
- 第一批計分採與 GS 相同的 binary64 算術順序；後續若改 Decimal，必須另列規則差異及版本，不能默默改動分數。
- 空白門檻代表 not_applicable；有門檻但資料缺失代表 unknown，不將來源缺失當通過或未抽取。
- bool、NaN、Infinity、數值溢位與不支援等級不得作為有效數值進入新核心。
- 知識圖 MCP 本次回報 Transport closed，依探索規則改讀相關 GS 精確來源；此工具故障不阻止可獨立的本地開發。

### 17.4 仍可獨立完成的待辦

1. 快照模型、本地物件持久化、分片續傳、封存、raw normalizer、mock collector 分頁證據／durable progress／觀測封存完成；仍需真實來源完整性／穩定 revision 對應、history／staging 清理與遠端 transport 契約。
2. 結構化健檢報告與快照差異已完成；數值 helper 及全員評分／加權總分／排名迴圈已做 GS 固定案例對照，仍需完整 header 映射與表格 mock 對照。
3. 本地持久任務、候選、依賴、預算、資源鎖與簽章／nonce 授權 gate 已完成；實際 D1／HTTP runtime／key registry 與遠端執行介面仍待離線契約驗證。
4. mock publisher、欄位 planner、十二欄 decoder、分片 staging、八 tab typed 格線與保護、版面遷移確認／archive、已知十一欄草稿遷移完成；遠端格式／維度／metadata adapter 與完整舊表格對照仍待完成。
5. 排刀核心、60 組 GS 對照、確認／輸出、合作式預算／checkpoint、本機 RSS reader 與五 tab 工作簿視圖完成；仍需 confirmed publisher eligibility、hosted 用量／deadline adapter、擴充邊界與完整效能驗收。
6. mock outbox、固定報告／路由編譯、路由／sender grant CAS、受授權 outbox／start permit、本地計算 worker 與 collector durable pages 完成；仍需真實 principal／provider 接線、遠端 executor 協定及其他命令 handler。
7. 本地 Worker／D1 契約與薄 GS／Vercel／Actions 入口的離線驗證，全部預設關閉外部整合。
8. A01–A22 已對應真實 JUnit 與逐項缺口（docs/spec/64-local-acceptance-evidence.md）；繼續補其本地未完成範圍，不以 local evidence passed 視為正式驗收。

### 17.5 第二批完成與新增契約（2026-10-15）

- domain/snapshots.py：canonical JSON、SHA-256、UTC 時間、完整 envelope、來源與設定版本、不可變訓練記錄、人工修正及快照差異。
- domain/reports.py：結構化缺項與相同結果派生的報告。未知擁有狀態不產生「請抽取」，缺資料的預覽標示不完整。
- application/healthcheck.py：以固定設定、資料、cohort、人工修正、顯示名稱與程式版本產生可重現 result_id；資料不足則拒絕產出可發布結果。
- 82 項本地測試通過，含多組參數／對照案例；Python 編譯及 Node 語法檢查通過。
- 凍結 GS helper fixture 來源 head 為 309b7c8c342e97d3cdb8441c23d0ab21c8f138b4，另保存實際來源檔與 fixture SHA-256。這是純函式對照，不代表完整試算表／排刀相容驗收完成。

追加的規格細節：

1. 門檻解析依舊 GS 移除內部空白；評分解析只去頭尾空白。不能用單一 whitespace 規則改變兩者。
2. Python 新版 sum 可能採用較精確的浮點加總；相容評分必須維持顯式逐項 binary64 累加。
3. JavaScript Math.round 不應以 floor(x + 0.5) 粗略模擬，因相鄰浮點值可能先被加法進位；本地已對照 0.5 下鄰值及大整數。
4. 新輸入契約只接受十進位 ASCII 數值及支援等級；十六進位、二進位、單獨加號、非有限值及全形數字不按 JS Number 的寬鬆行為解析。
5. trait_1／2／3 必須附 function_id。原始詞條完整保留在 function ID 映射，換追蹤順序重新投影即可。
6. 人工修正的 0、明確空白、停用與 clear 分開處理；重複目標阻擋。養成修正不能推定角色已持有，也不能新增未涵蓋身份。
7. 缺資料、未知擁有狀態、空名單或缺少有權重的數值不得被轉成正常 0 分結果。真正清空／停用聯盟需獨立確認流程，不能和擷取失敗共用分支。
8. 快照差異只比較同聯盟、同字典版本、可比較的共同 metric／function ID。新資料補齊、詞條換位及數值表示差異不算養成進步。
9. 舊報告不輸出突破缺項；目前保留文字相容行為，突破檢查仍存在結構化 assessments 中，不能視為達標。
10. 報告顯示文字會移除控制換行並處理引號、反引號及 @，防止顯示名稱破壞程式碼區塊或插入額外 mention。
11. JSON envelope 不接受重複 key；內容變更必須符合 expected SHA-256。秘密欄位防呆不是完整 collector 欄位白名單的替代品，收集器仍待實作。
12. Node 子程序只執行固定原始碼 fixture 與合成 JSON，清除 provider 秘密及 NODE_OPTIONS；其 VM 不提供 require／fetch。Python socket guard 不應被描述成能攔截任意子程序網路。

### 17.6 第三批完成與新增契約（2026-10-15）

- application/tasks.py 定義不可變 TaskSpec schema_version=1、業務指紋、執行租約、發布權限及預算需求。
- adapters/local_state.py 與 coordination/local_migrations/0001_state.sql 提供明確路徑的本地 SQLite 狀態庫，不匯入正式設定或憑證。
- 候選設定 CAS、設定 head 與重算 fanout 在同一交易提交；單聯盟失敗不取消其他聯盟，設定發布不會發送 Discord。
- 已完成持久 checkpoint、依賴、有限重試、取消、程式版本限制、過期執行者隔離、派送票證、逐工作簿發布進度與未明發布恢復。
- 全部 124 項本地測試通過，含雙連線競爭、重啟恢復、交易回滾、預算及舊派送回呼案例。這是 SQLite 證據，不代表 Cloudflare D1 相容驗收。

追加的規格細節：

1. 收集新觀測值與人工發送必須帶明確 intent_id；不能因相同設定而永久命中舊工作。排程另外以 UTC 時槽去重。
2. 執行租約可過期重領；發布結果不明則持續持有全域與實際工作簿鎖，時間經過本身不構成解鎖理由。未完成發布時阻擋設定 head 或 desired target 切換。
3. 回滾到已存在的設定可重用不可變計算結果，但必須重新發布，不能因舊任務 succeeded 而略過工作簿更新。
4. hosted 執行只能領取已保留預算的派送票證；保留發生在外部啟動之前。Vercel CPU 與記憶體預算必須一起成功，不能部分保留。
5. 派送是否啟動不明時保留預算，不自動退回；實際超出預估仍記帳並阻擋後續超額工作。回呼綁定派送時保存的 generation、run ID 與 worker，避免重用 worker 名稱接管新工作。
6. waiting_for_data 不能靠 resume 改變舊工作的不可變輸入；資料補齊需建立帶新版本的工作。auth／capacity 等等待則可恢復。
7. 發布恢復沿用相同 commit_id，檢查所有資源的讀回證據；部分成功時保留全部鎖與逐資源進度。SQLite migration 保存 checksum，已套用檔案變更即拒絕啟動。
8. 目前「舊 writer 已停止」、實際用量與工作簿 commit 讀回仍由可信本地呼叫者提供。未來 provider adapter 必須取得可驗證證據，不能讓外部請求自行宣稱成功或 writer 已停止。
9. 此批尚未實作 mock 工作簿格子寫入、正式 Sheets publisher、HTTP 身分驗證、D1 adapter 或雲端啟動。下一批須用可注入失敗的 mock Sheets 驗證實際發布流程，不能以狀態庫單元測試取代。

### 17.7 第四批：mock 生成區發布（2026-10-15）

- 新增本地檔案工作簿與 publisher，將封存結果、發布權限、生成內容與讀回驗證串接；僅使用合成資源。
- 驗證跨工作簿部分成功、寫入前／後失去回應、SQLite 與工作簿 adapter 重啟恢復、同 commit 跳過已完成資源。
- 追加：commit 標記必須和實際生成內容 hash 一起驗證；同 commit 不得替換 payload。publisher 不接受與封存結果不同的內容／layout manifest。
- 追加：mock 人工區與生成區分離，生成區採完整替換避免舊尾端資料殘留。layout revision 衝突須拒絕覆寫並保留未明狀態。
- 這批是邏輯生成區模型，尚不包含正式格子座標、格式、公式、保護區、分片 staging 或 Google HTTP 模擬；不能宣稱完整 Sheets 相容性。
- 追加：封存 manifest 以 canonical JSON 比對，不能用 Python 的寬鬆數值相等取代不可變內容契約。全套 135 項 mock 測試通過。

### 17.8 第五批：本地快照 transport（2026-10-15）

- domain/chunks.py 實作 zlib／SHA-256 manifest、32 KiB 分片、最多 512 片與 16 MiB 物件限制；原始／壓縮大小均受限制。
- adapters/local_chunks.py 支援持久化任意順序上傳、缺片查詢、重播、重啟續傳與完整封存。未封存不得 read，已封存每次讀取仍重新驗證。
- 追加：封存必須拒絕不完整序號、hash／精確大小不符、尾隨壓縮資料、未結束 stream 與超出宣告解壓大小。解壓器輸出有界，不能先無限制解壓再檢查。
- 追加：同一物件首份 manifest 固定；重新壓縮結果若不同需沿用已登記版本，不得混傳不同分片。SHA 與 schema／大小欄位皆嚴格驗證。
- 測試使用合成資料並涵蓋 sealed 快照匯入本地狀態庫。此功能尚未接上 HTTP／D1，也未取代工作簿 staging；儲存配額、保留清理與遠端封裝仍待實作。
- 全套 151 項本地測試及新增模組的 Python 語法檢查通過。

### 17.9 第六批：穩定欄位與 layout 確認（2026-10-15）

- domain/layout.py 提供 Column／Layout／LayoutPlan，按穩定 ID 規劃更新、封存、恢復與附加；確認綁定完整計畫、base revision、設定及程式版本。
- mock 工作簿保存變更前的結構與生成資料，確認失敗保持原狀；未實作真正刪除。
- render_columns 輸出精確 system 欄範圍、型別化值、結構化狀態顏色與尾列清除；字串不轉公式，拒絕非有限數值及不精確大整數。
- 追加：混合人工欄的同表不能直接按新名單重排生成列；必須核對舊 member ID 順序。人工資料應獨立成區，以 ID 關聯，避免備註跟著位置錯配。
- 追加：既有實體欄保持位置，desired 順序只決定新欄附加順序。屬性分組重排需另有可確認的遷移計畫，不能假稱目前已支援。
- 目前是單列標題的新模型，尚需舊 GS 多列／12 欄模板轉換、隱藏／維度請求、格子級 mock 與中央 layout publisher 串接；不以純模型測試宣稱完整表格相容。
- 全套 167 項本地 mock 測試及新增／變更模組的 Python 語法檢查通過。

### 17.10 第七批：原始來源與訓練正規化（2026-10-15）

- domain/normalization.py 定義原始欄位白名單與來源 outcome／request／options coverage；不匯入舊網路／登入模組。
- 本地合成資料驗證完整／部分／失敗／auth_required，區分 unknown、not_owned 與 owned 缺 detail；成員依 area／member ID，角色依 name_code。
- 全部取得詞條保留為 function ID 映射，計分／門檻更換追蹤可重用快照；訓練快照綁定 raw_snapshot_id 與 normalizer_version。
- 追加：突破缺 grade／core 不再補 0；部分裝備覆蓋不把觀測總和或不存在詞條當完整 0。未知 state effect 保留未知。
- 追加：參照角色字典必須完整且釘版本，不可只用當下追蹤角色清單驗證整份 ownership 回應；重複來源身份與互相矛盾資料阻擋正規化。
- 追加：裝備與收藏品轉換沿用已讀本地來源；五位詞條前綴、首個 function_details、百分比取絕對值累加與兩位小數為 normalizer v1 相容契約。尚未驗證未來 API 變種。
- 尚無線上 collector、分頁／完整來源證明、參照資料發布與歷史刀轉換；trusted coverage flag 不代表已驗證遠端完整性。
- 全套 193 項本地 mock 測試及新增模組 Python 語法檢查通過。

### 17.11 第八批：本地報告 outbox（2026-10-15）

- DeliverySpec 固定確認後的報告版本、成員、路由版本、thread、內容分段與 mention；request／intent 持久去重。
- 追加 SQL migration 與 LocalOutbox，交易領取、逐段狀態、最多三次明確拒絕重試、provider message ID 與 sent_at；mock Discord 僅保存本地合成訊息。
- 追加：每段按 UTF-16 2000 units 上限驗證，不能按 Python 字元數錯估 emoji；只有第一段允許明確使用者 mention，其他自動 mention 皆停用。
- 追加：未知發送不因超時或重啟自動重送；後續分段等待。核對須確認舊 sender 停止與完整 payload／target receipt，或確實未送出，才能結案或重排。
- 程序中斷的 sending 可以先隔離為 delivery_uncertain，但隔離不代表已證明 writer 停止；重新領取須另通過可信核對。
- 尚需已發布報告／路由編譯、完整性驗證與格式保留分段、遠端授權及實際 provider adapter。本地 evidence 不能代替正式平台發送驗收。
- 全套 205 項本地 mock 測試及 Python 語法檢查通過，包含兩連線競爭領取同一訊息。

### 17.12 第九批：排刀輸入與階段模擬（2026-10-15）

- domain/raid.py 實作固定成員身份、三 slot 原始指定、五傷害覆寫、多行歷史及有限階段／IV-5 模擬。
- 追加：指定刀無效時只回傳 warning，不清除原始輸入，這是相較舊 GS 清空無效 slot 的明確遷移差異。
- 追加：真實傷害／HP 以有界 decimal 字串運算並固定局部 precision；估傷保留原 binary64 三次方及 JS 取整，億轉真實值用整數乘法。
- 追加：source_locked_stage 與本次 stage 分開，避免鎖定 IV 因暫時 stage=0 在下一輪被改派；同名最後以成員 ID 打破平手。
- 追加：歷史無效行／行數差異保留 warning，不可被上層當完整來源。階段不接受小數 floor，Python Unicode 排序也尚未證明與 GS localeCompare 完全相同，完整移轉須另處理。
- 凍結 8 個 GS helper 與 provenance，在隔離 Node VM 對照 100 組估傷及 40 組階段案例。全套 222 項本地測試與 Python／Node 語法檢查通過。
- 候選群組、輪次、補洞、改派、指定優先輪替、燃燒／鐵甲交換、草稿確認與完整效能仍未完成；A15／A16 不能標成完整通過。

### 17.13 第十批：來源池與主輪次選刀（2026-10-15）

- domain/raid_planner.py 固定完整 RaidProblem 與 problem_id；RoundSelection 綁定輸入版本及 algorithm_version，不能因輸出碰巧相同忽略來源版本。
- 完成有效指定優先的來源池、群組、自動候選、三輪高同步主選、低同步剩餘補洞、跨群組燃燒／鐵甲覆蓋與嚴格失敗才放寬的衝突 fallback。
- 每次選入都執行完整階段模擬；自動候選拒絕增加缺口／同缺口增加剩血或產生第三階段溢傷。原始指定與歷史保持不可變。
- 追加：同分元素順位需明確保存 element_tie_order，不能依機器 locale 隱式改變；預設為核心 ELEMENTS 順序。與舊 GS 同分排序差異仍待完整對照，不能默認相容。
- 凍結 22 個 GS 來源池相關函式並保存 provenance；25 組唯一完整排名／傷害對照通過。全套 236 項本地測試通過，含 12 成員粗略時間回歸。
- 本批不是最終排刀：仍需穩定化、改派／溢傷改善、指定刀優先讓位、燃燒鐵甲交換、未落地恢復、剩餘補回、草稿確認與輸出排序；A15／A16 保持未完成。

### 17.14 第十一批：排刀穩定化與完整計算對照（2026-10-15）

- domain/raid_finalize.py 加入有限改派、重複狀態停止、指定讓位、燃燒鐵甲交換、未落地恢復及剩餘來源最終補回；每個方案重新模擬。
- 交換／恢復保護已落地指定、總落地數、缺口與所選溢傷指標；歷史與指定目標不移動。FinalRaidPlan 綁定問題、來源選刀與演算法版本。
- 發現並修正前批判讀：舊 GS 的第三階段溢傷 gate 使用已截斷 effectiveTrue，通常為 0，不等同真實浪費。初期預設 legacy_effective 保留相容，raw_damage 作明確版本化選項；真實浪費另存，不默默改正式規則。
- 追加：同成員重複五王的驗證應按有限／IV 來源鎖判斷，不按輸入 tuple 的列出順序，避免指定讓位時錯誤拒絕合法雙 IV-5。
- 隔離凍結 GS 完整計算來源，60 組合成最終 member／boss／stage／kind／damage 對照通過，包含歷史、反向、固定、重複五王與全清尾段。尚未證明所有 locale／最適性或最差執行時間。
- Python 交換／恢復的已落地指定與刀數保護比部分 GS helper 嚴格，明列為仍需擴充比較的差異，不假稱所有輸入完全相容。
- 草稿確認、輸出與指定區呈現、deadline／checkpoint 及完整 A15／A16 仍待完成。
- 全套 244 項本地 mock 測試及 Python／Node 語法檢查通過。

### 17.15 第十二批：排刀草稿確認與結構化輸出（2026-10-15）

- domain/raid_view.py 保存成員 ID、五王逐刀來源／傷害／階段與新刀數，依有新刀、同步、名稱及 ID 排序；原始指定保持獨立不可變。
- 追加：渲染前重模擬並驗證來源池、指定身份與自動估傷；不能只靠結果 hash 接受自相矛盾內容，也不能刪除必要 warning 迴避缺刀確認。
- LocalRaidPlans 與 0003 migration 保存 input head／revision、draft、confirmation 及 request alias；確認同時 CAS 輸入與上一確認，全部只在本地 SQLite。
- 追加：新排名只更新依據與新草稿；已確認內容不替換。過期／是否目前確認是獨立動態狀態，不修改原確認物件。
- 追加：歷史與新刀混格保留逐行來源，全歷史才整格灰色；多刀 IV 共用單一階段顯示。億顯示採精確正數 half-up，原始真實傷害仍保存字串。
- 全套 251 項本地 mock 測試通過，含兩連線同 base 確認、重啟、過期、同名成員與警告確認；Python 語法檢查通過。
- 尚無遠端 actor 授權、工作簿確認 UI、固定路由、精確座標／rich text publisher 串接與 deadline／checkpoint；不以本地確認代表正式生效。

### 17.16 第十三批：執行預算與階段續跑（2026-10-15）

- 新增 domain execution hook、ComputeBudget 與 run_raid_job，固定問題／程式版本後執行選刀、最終整理、檢視與本地草稿。
- 以 start／selected／finalized 階段保存不可變 checkpoint；程序重啟可跳過已完成階段。schema、演算法及 selection／final 綁定不符拒絕續跑。
- 追加：時間、操作次數、記憶體 reader 未知／上限等限制中止時回 capacity_blocked，不完成結果、不自動確認或發布。單階段太長時需轉較長 executor，不能無限重試同一短預算。
- 追加：每 15 秒刷新執行租約；合作式 hook 在例外後 reset。預留收尾時間不代表能攔截 OOM 或任何外部 blocking 呼叫，仍須限定物件大小並驗證實際 runtime。
- Vercel 類型本地契約要求時間 <=240 秒與明確 memory reader／<=1500000000 byte 上限；沒有建立或呼叫任何 Vercel 服務。
- 全套 264 項本地 mock 測試及 Python 語法檢查通過，涵蓋假時鐘中途停止、記憶體未知、容量中止、heartbeat 間隔及重啟跳過選刀。
- 實際 RSS／平台剩餘期限 adapter、遠端 checkpoint transport、CLI／雲端入口及最差情境 SLA 仍待完成。

### 17.17 第十四批：同源設定與離線 CLI 整合（2026-10-15）

- 新增 structured mock 標準表 reader、reconcile_standard 與 run_healthcheck_job，串接候選、固定快照、名單／修正、計算與 mock publisher。
- 回寫的 standards 直接來自計算採用的同一 config snapshot；測試門檻 5 候選後草稿改 9，原確認內容仍固定，另行發布 9 可重用原 training snapshot。
- 追加：候選業務身份需排除純觀測時間，含 scope／base／schema／reference／payload；0004 migration 加入 semantic_id，並在建立時沿用相符舊候選，避免 reconcile 反覆產生同內容候選。
- 追加：空白列可略過，空表不能當有效設定清空發布；未知字典身份／欄位及無效 metric 阻擋。此批 structured mock 格式尚未對應正式 GS 橫向標準格。
- 新增 python -m executors.local demo／status，只使用明確 mock 目錄。demo 拒絕覆寫非空資料夾，status 需 mock marker 與既存本地 DB，不載入正式設定／憑證。
- 全套 275 項本地 mock 測試通過，包含實際 CLI、候選固定、門檻重算不重抓、同名成員、漏事件 reconcile 與缺資料保留舊結果。
- 正式表格 reader、一般 worker CLI、路由／actor 授權、collector／遠端協定及精確格子發布仍待完成；A01／A02／A06／A11 僅新增相應本地證據，不宣稱正式環境驗收。

### 17.18 第十五批：舊標準格解析與同角色多類型（2026-10-15）

- 新增 LegacyCatalog／read_legacy_standard／reconcile_legacy_standard，離線解析八／九欄名單與六列、每角色十二欄標準；門檻／係數／權重依已讀本地 GS 契約。
- 追加：名稱與詞條須唯一對應 pinned ID，可用保護的角色 ID 消除歧義；已知名稱／ID 矛盾、錯位、未選詞條卻有值、雙屬性副本不一致都阻擋候選。
- 發現並修正模型遺漏：既有 Python 支援同角色輸出／輔助，部分 GS 按名稱首項處理。新 model_version=2 共享 name_code 養成與標準，分別保存宣告類型與追蹤類型，角色原始分及報告只產生一次。
- 同角色多類型的共用設定／詞條／顯示必須一致，追蹤可分開；v1 保持可讀，新型別擴充不能假冒 v1。正式採用需搭配理解 v2 的 release，舊正式入口未更動。
- 全套 293 項本地 mock 測試及 Python 語法檢查通過；橫向標準已串接固定候選、計算與 mock 回寫，候選後再編輯不改既有確認。
- 真實 grid／merge fetch、十一欄遷移、完整格式保護與字典擷取仍未接上；不以本地模板 decoder 宣稱所有正式表格相容。

### 17.19 第十六批：已發布報告與整批發送候選（2026-10-15）

- 新增 ReportSendCandidate／compile_report_candidate，從成功發布且仍為目前 desired target 的健檢結果編譯 DeliverySpec；固定 job／result／config／training／code 與獨立路由快照，空報告不發送，缺路由或不完整報告拒絕。
- 追加遺漏契約：發送確認不能只驗候選 hash，必須重新從 sealed 結果與固定路由編譯整份內容，拒絕文字與 target 篡改；scope／intent 固定候選，不允許背景重讀後替換。
- 格式分段每段保留完整 cs fence，以 UTF-16 units 限制，換行優先且不切斷 emoji；mention 只在首段明確授權。
- 0005 migration 持久保存候選與批次 request。確認時驗來源並將所有成員原子入列；後一筆衝突會全批回滾。已確認重播保留既有內容與送出狀態，不因新 head 再次建立發送。
- 全套 304 項本地 mock 測試通過，包含真實本地 SQLite 交易、mock 發送與重啟；未連接正式試算表、Discord 或任何部署平台。
- 路由由可信本地 caller 提供；真實 actor／thread 授權、route head 與雲端傳輸仍待開發。此批完成 §17.4 報告編譯／固定路由資料契約的本地部分，不視為正式權限驗收。細節見 docs/spec/55-report-delivery-compilation.md。

### 17.20 第十七批：通用本地計算 worker（2026-10-15）

- 新增 work CLI，僅在已標記 mock 目錄領取同 code／local 的健檢與排刀，每次最多 100 工作；預設只計算，mock 發布需明確開關。
- 追加：command 白名單必須在 SQL LIMIT 前套用，避免不支援工作塞滿前 100 筆使可執行任務飢餓。未指定篩選的舊 caller 行為保留。
- 健檢 computed 可重啟後從同 sealed manifest 發布；未知發布隔離且不自行解鎖／重寫。排刀從封存 hash 還原 typed problem，budget 中斷與 checkpoint 可明確恢復，完成不自動確認。
- local_tests/test_local_worker.py 新增 8 項流程驗收；含實際 scrubbed-env CLI、105 筆佇列前置、重啟、未知發布與排刀 resume。只用合成資料與本地檔案。
- RSS／健檢迴圈 deadline、collector、其他命令與遠端 runtime 尚待實作；工作數上限不可當成硬性執行時間限制。細節見 docs/spec/56-offline-worker.md。

### 17.21 第十八批：本地資源讀取與健檢中斷（2026-10-15）

- 新增本機 resident memory reader 與 worker 預設 budget，排刀與健檢共用 heartbeat 檢查；讀取記憶體失敗視為 unknown，不當作零使用量。
- 健檢 coverage／報告／評分迴圈與結果封存前接上合作式 deadline／操作次數／記憶體檢查；中斷進 capacity_blocked，保留舊 workbook，不封存半份結果。
- 追加：健檢中斷尚無 stage checkpoint，resume 從同 pinned 輸入重算；整個程序 RSS 與其他 Unix peak fallback 不能當單一工作精確用量。
- 7 項新增 mock／本機測試涵蓋四處健檢中斷、恢復成功、本機 memory 讀值、小上限與 OS 讀取失敗。
- 單次 JSON／排序／阻塞 I/O 仍不能合作式中止；硬性子程序隔離、平台 deadline 與 hosted 量測證據仍待驗收。完整契約見 docs/spec/57-local-compute-resources.md。

### 17.22 第十九批：mock collector 與來源分頁證據（2026-10-15）

- 新增 CollectionPort／MockCollection 與 run_collection_job，依不可變 roster／reference 輸入產生 raw→training；完整且無 issues 才封存成功 result，不自動發布試算表。
- 追加：不能僅信任 caller 的 complete 字串。分頁需固定身份／source／reference、同 revision／total、無重複與循環、terminal 數量正確，details／effects 精確覆蓋 request。partial 不推定 not_owned，缺 effects 不推定零詞條。
- 對 auth 停止該成員後續請求；逾時／缺頁有限重試，結構矛盾隔離。白名單後才保存 rows hash，cursor／revision 只保存 hash。
- 新增 19 項 mock 驗收；未呼叫真實 API、未讀取憑證或正式設定。
- provider 仍是可信 port，未證明真實 API 有 revision／total 或跨 source 原子性；實際 adapter 必須提供真實證據，不得虛構完整性欄位。durable 分頁 checkpoint、roster 擷取與傳輸仍待完成。契約見 docs/spec/58-offline-collection-evidence.md。

### 17.23 第二十批：本地控制請求授權與重放契約（2026-10-15）

- 新增 HMAC envelope 與 LocalRequestGate，限制 64 KiB、時間窗、nonce、body digest、request_id、命令／scope／實際 workbook 權限。只允許健檢／排刀計算，不開放發布或發送。
- 追加：workbook 權限必須從 sealed 計算輸入解析，不能信任 caller 另傳 resource 清單；排刀須綁 scope input head。所有重播重新檢查目前 principal／key policy。
- 0006 migration 保存 nonce／request 去重，金鑰僅在可信 runtime 記憶體；replay 紀錄與 job 提交之間 crash 可用固定 request key 補完，不先回假成功。human actor 明確 unknown。
- 新增 21 項 mock 驗收，包括 body／簽章篡改、撤銷 key、過期、nonce 重用、重複 JSON key、實際目標權限、重啟與中途 crash。
- 尚無公開端點、正式 key registry／snapshot ingestion 授權／D1 adapter；不可把服務簽章當成編輯者身份。完整邊界見 docs/spec/59-local-control-request-gate.md。

### 17.24 第二十一批：可取消與續傳的 workbook staging（2026-10-15）

- 新增 WorkbookStage／LocalWorkbookStaging／StagedSheets，固定 result／resource／schema／base commit 與分片 manifest；全部 sealed 才可經既有 publisher 啟用。
- 本地 worker mock 發布改走 stage，準備／缺片／取消不碰舊結果，schema 變更拒絕啟用，人工輸入使用啟用時的最新 mock 內容。
- 追加：啟用前即持久保存 activation_uncertain，程序消失不能變成自動 retry；stage 回讀成功只更新 stage，不自行解除 coordinator 資源鎖。
- 新增 6 項 mock 驗收，包含實際多片上傳重啟、before／after timeout、取消、人工區保留與 schema 變動；仍未使用正式試算表。
- A21 新增本地流程證據，但 Google staging tab、逐格格式／保護／merge 與跨 API 啟用原子性尚未驗證；本地單次檔案替換不可代表正式能力。細節見 docs/spec/60-local-workbook-staging.md。

### 17.25 第二十二批：GS 全員評分迴圈對照（2026-10-15）

- 凍結本地 GS 實際角色計算、屬性／輔助加權總分與 competition rank 函式，保留來源 head／檔案／fixture hash；沒有執行 GS 服務或讀取正式試算表。
- 50 組固定合成 cohort 精確比對 Python 每成員／角色分數、群組分數與排名；包含空洞列的超高無關值、同分、零值、多屬性／類型、浮點權重與負係數。
- A03／A04 增加完整計算迴圈證據；header config／nickname 與 sheet range 仍為 mock 邊界，不代表完整工作簿結構相容。詳細界線見 docs/spec/61-gs-cohort-scoring-parity.md。

### 17.26 第二十三批：本地備份與還原 fence（2026-10-15）

- 新增 bounded mock workspace backup／restore CLI，SQLite backup API 保存一致 DB，manifest 固定檔案 hash／大小／release label／migration map，拒絕 active／unknown writer 與 sender。
- 追加：還原不能等同接手 writer。副本預設 restored_read_only，worker 一律拒絕執行，status 可讀；本地不提供自動移除 fence。
- 還原僅新目錄，驗證後才寫資料，禁止越界 ZIP 路徑與既有檔案覆寫；保留已送 outbox／message ID，不重新排入已完成發送。
- 新增 8 項 mock 驗收及實際 scrubbed-env CLI；A22 增加本地資料還原證據，尚非正式 release binary／跨平台 fencing／降版 migration 驗收。詳見 docs/spec/62-local-backup-restore.md。

### 17.27 第二十四批：保守物件保留與清理計畫（2026-10-15）

- 新增不可變 ObjectRetentionPlan 與 LocalObjectRetention；先規劃、指定 hash 確認，再在同一交易重驗 inventory／root 後刪除舊孤立 objects。
- 追加：root 不限 SQL FK，還需解析 job／outbox／候選 JSON 與 training→raw 等嵌入引用，取傳遞閉包。歷史設定／任務不自動裁切，不以過期 lease 判斷可刪。
- 0007 receipt 使清理原子且可重播；新引用／新物件使舊計畫整批拒絕。壞 hash、超過 10000 objects／100000 root rows／bytes 邊界均停止。
- 新增 5 項臨時 mock DB 驗收，沒有刪除專案檔案、正式試算表或遠端資源。
- chunk／staging／archive 清理、歷史裁切與遠端 D1 預算仍待完成；新增外部引用來源前須定義 pin，不能直接假設沒有引用。詳見 docs/spec/63-local-object-retention.md。

### 17.28 第二十五批：逐項驗收證據與缺口報告（2026-10-15）

- A01–A22 逐項綁定明確 pytest node IDs，從真實 JUnit 結果生成本地證據；缺案例／skip／任一參數失敗都不能算通過，保留 suite 整體失敗狀態。
- 本輪全套 383 項 mock 測試通過，22 項都有對應案例通過；報告明確 is_production_accepted=false，逐項列出 remaining，不能推導為全部需求完成。
- 追加 4 項報告 importer 驗收，防止空紀錄、skip 或部分參數失敗被當成通過。報告入口只讀既有 JUnit，輸出新檔，不載入正式設定。
- 本地未完成範圍與真正外部依賴分開記錄；仍可自主推進 renderer、薄 GS／hosted／D1 離線契約、checkpoint／registry／效能等，不進入 blocked。證據見 docs/validation/2026-10-15-local-acceptance.json，契約見 docs/spec/64-local-acceptance-evidence.md。

### 17.29 追加：外部請求不得自創 task family（2026-10-15）

- 稽核發現 signed task 原可自選 family，可能使舊版本輸入避開 healthcheck／raid desired target 的比較；限制 signed command 使用固定 family，可信本地 API 的一般能力保留。
- 新增入口測試驗證有效簽章也不能繞過 family policy，且拒絕時不寫入 replay receipt。這是本地授權契約修正，無正式環境變更。

### 17.30 第二十六批：薄 GS 提示到 Python 候選（2026-10-15）

- 新增未部署、預設停用的薄 GS onEdit／timer 橋接，只過濾事件與送簽章 reconcile 提示，不讀資料格、做業務計算或直接發布結果。
- Python gate 固定 reconcile family／sealed route／principal 權限，本地 worker 讀 mock source 產生固定候選；明確確認 config 仍是另一步，不因 onEdit 自動發布。
- 新增 9 項離線測試，Node 執行真實橋接 HMAC，再由 Python gate 驗證、worker 產生相同候選；驗證 HTTP accepted 不等於計算成功、timeout 不盲重試與停用不讀 key。
- A11 補上跨語言端到端本地證據；無正式 trigger／Properties／URL／部署，HTTP runtime、coalescing 與 durable hint queue 仍待完成。詳見 docs/spec/65-thin-gs-reconcile-bridge.md。

### 17.31 第二十七批：控制 HTTP 邊界的離線串接（2026-10-15）

- 新增預設停用的 transport-neutral handler，限制 route／method／body／header／envelope 大小與重複 key；只有持久受理 job 才回 202 accepted。
- 薄 GS 測試改走 HTTP handler→簽章 gate→本地 worker→固定候選，不啟動 socket 或使用真實 URL。
- 新增 11 項 mock 驗收，確保停用不接觸 gate、錯誤脫敏、受理與完成不混淆；Worker／D1／TLS／限流與平台 runtime binding 尚未實作。詳見 docs/spec/66-offline-control-http.md。

### 17.32 第二十八批：控制請求容量與時間 watermark（2026-10-15）

- 0008 migration 加入每 principal 分鐘／全域日的新 envelope 持久預算；alias 新 nonce 也計數，精確重播不重扣，超限回 429 與 Retry-After，交易全部回滾。
- 追加：清理 nonce 前必須保存 server time watermark，時間倒退停止受理；只有已超簽章時間窗的 nonce 可刪，歷史 request 去重不能一併忘記。
- 新增 5 項 mock 流程／兩連線驗收；未讀取正式用量或連線平台。這是本地保守入庫上限，不替代 provider CPU／D1 行數／帳戶共享預算。詳見 docs/spec/67-local-control-capacity.md。

### 17.33 第二十九批：本地 profile 找出並修正 reader 配置增長（2026-10-15）

- 新增有界合成核心 profile，保存 wall／CPU／取樣 RSS／check 次數與固定結果 hash，保留首次失敗與修正後 JSON。
- 實測發現 Windows RSS reader 每 check 重建 ctypes 型別／pointer signature，30 人／200 角色在約 6 秒達 512 MiB；快取 API／型別後，同輸入三次健檢約 1.47–1.83 秒、最高約 41.1 MiB，結果 hash 一致。
- 追加：資源 reader 自身必須接受高頻穩定性驗收，不可只測一次讀值為正。新增一萬次讀取、profile 結果穩定與範圍測試，共 3 項。
- 30 人合成排刀約 0.48–0.50 秒、三次同 hash；這不是 hosted／完整 trait／歷史矩陣或 billing 容量驗收。詳見 docs/spec/68-local-core-profile.md。

### 17.34 第三十批：同源八 tab 健檢視圖（2026-10-15）

- 新增 typed grid／八 tab WorkbookView，從固定 config／training／input hash 產生標準、五屬性練度、評分、報告，與計算 manifest 一起封存及 mock 發布。
- 標準六列／十二欄、合併、詞條 function ID、隱藏 composite identity、格式與顏色均有明確模型；same override 只影響投影，不改原始 training。
- 追加：renderer 必須驗 names／overrides／roster input hash，不得計算後偷偷更換來源；分數為 0..100 NUMBER，不當百分比。formula-like 字串只能轉 literal stringValue。
- 新增 8 項 mock 驗收；selected 標準可經既有 decoder 回讀，實際 mock publication 可還原相同 view_id。
- 新 training／score view schema 不宣稱所有舊版 header 相容；protected flag 尚非遠端 enforcement，真實 grid materialization／舊尾列清除／遷移仍待完成。詳見 docs/spec/69-generated-workbook-views.md。

### 17.35 第三十一批：mock 實體格線與保護（2026-10-15）

- 回寫原子保存 generated payload 與 derived typed grid；八 tab 的顏色、note、merge、尺寸、凍結、隱藏欄及保護均可回讀核對，縮短列完整清尾。
- 人工 tab 與 inputs 保留；產生 tab 禁止透過 mock 協作者入口編輯，初次 materialization 有 key／title 衝突即拒絕。
- 追加：回讀不能只驗 payload hash，必須核對實際格線與格式；merge 非錨點的 note 也不得隱藏。結構不同或移除 view 必須走明確 migration，不可一般回寫暗中採用。
- 此批仍是本地 JSON workbook 的 enforcement，沒有宣稱遠端 Google protection／交易已完成。後續補 migration preview／確認與 archive。詳見 docs/spec/70-mock-physical-grid.md。

### 17.36 第三十二批：版面遷移預覽、確認與 archive（2026-10-15）

- typed grid 結構變更須預覽固定 source／target payload、grid、commit、config、code；確認後仍須通過原 publisher claim 才回寫。
- 原子封存完整舊產生區並消耗核准；核准本身不改結果，基底或目標任何變更即失效，人工 tab 保留並重新檢查衝突。
- 追加：每本 20 份 archive 上限，達限停止而非暗中刪歷史；before／after fault 不可產生半套 archive 或重複封存。
- 新增 17 項 mock 驗收含 sealed publication／unknown recovery；十一欄首次遷移與遠端多請求原子性仍未完成。詳見 docs/spec/71-mock-grid-migration.md。

### 17.37 第三十三批：舊十一欄標準草稿的明確遷移（2026-10-15）

- 從明確物理寬度、角色 ID、merge／note inventory 預覽十一→十二欄逐格映射，保留技能／裝備／收藏品／詞條，新增突破保持空白。
- 先用既有 decoder 驗證目標 config，確認綁定 source／catalog／config／code；本地原子封存舊來源並更新草稿與 layout_revision，不發布正式標準、不排計算。
- 追加：不可把缺 metadata 或隱藏資料的破損模板當正常舊版遷移；同計畫重播不得覆蓋後續草稿。26 項本地 mock 測試涵蓋依賴變更、資料保存、候選邊界與 archive 上限。
- 仍未取得遠端 grid／格式／寬度／保護 metadata，不能以這個已知合成模板宣稱所有舊工作簿相容。詳見 docs/spec/72-legacy-standard-migration.md。

### 17.38 第三十四批：排刀五 tab 唯讀視圖（2026-10-15）

- 從固定 problem／final 產生總覽、逐刀明細、原指定刀鏡像、警示與 BOSS 狀態，和 job 結果封存；不自動確認或發布。
- 追加：混合歷史與新刀不得用單一格色掩蓋來源，總覽保留註記且逐刀明細分色；大額真實傷害保持 decimal 字串。
- 確認結果 renderer 重讀確認當時的 problem／final，核對原 view hash；新輸入只使狀態 stale，不換掉確認內容。六項新案例及既有 job 驗證涵蓋來源、格式與確認邊界。
- 尚未串接正式排刀工作簿路由／publisher eligibility，不能以模型 protection 宣稱遠端保護生效。詳見 docs/spec/73-raid-workbook-view.md。

### 17.39 第三十五批：養成快照沿用與補抓 planner（2026-10-15）

- 純規劃器以同一 normalizer／計算核心驗證可沿用性，固定 config／raw／reference／prefix map／roster／override／code hash；缺項按 member／source／role／option 合併成 deterministic requests。
- 追加重大遺漏：ownership complete 必須綁定當時的角色 universe。新 collector 保存 reference_role_ids，normalizer 拒絕擴大／縮減，planner 對舊缺證據 raw 保守停止，避免新角色被當未持有。
- 十二項新 mock 案例覆蓋選詞條／改門檻零補抓、精確 effect、options coverage、突破、auth、override、新人、reference 與 universe 邊界。
- 尚未做 reference 持久發布 head、補抓 executor 或新舊來源拼接，不對正式 provider 發請求。詳見 docs/spec/74-training-reuse-planner.md。

### 17.40 第三十六批：不可變 reference 與本地 CAS 發布（2026-10-15）

- reference 字典以內容 SHA 作版本，包含角色／prefix／function／alias／雙屬性；同內容不同 capture time 共用版本，讀取時重建核對。
- 0009 migration 與 LocalReferences 保存固定候選、validation code、明確確認、head CAS、receipt 與 request 去重；兩連線只允許一個版本跨越相同 base。
- 追加：重播舊 reference 發布只能取回 receipt，不得把新 head 倒回；reference 發布不能連帶重標 raw 或發布 config。舊候選／publication 均須留在 retention roots。
- 13 項新 mock 案例及既有 retention／backup 驗證通過；provider 字典擷取、reference transition 衍生投影、補抓 executor 與外部 endpoint 仍未接線。詳見 docs/spec/75-local-reference-registry.md。

### 17.41 第三十七批：Reference 相容變更的衍生投影（2026-10-15）

- source／target reference 經內容驗證後，別名更新、重排或移除既有角色／prefix 可從原 raw 產生 target-version training；raw 完全不改，衍生物固定完整 lineage。
- 追加：reference 相容不代表資料完整，unknown 仍由同一計算核心阻擋；新角色或改動 prefix 定義不可沿用舊 absence／數值，auth 也不能因投影被升級。
- 十項本地 mock 案例驗證固定 plan、衍生計算、unknown／auth、拒絕來源置換及原 raw／雙 reference 的 retention；normalizer 加入逐 member／role 合作式預算檢查。
- 尚不自動排補抓、合併 provider 資料或發布。詳見 docs/spec/76-reference-projection.md。

### 17.42 第三十八批：本地計算子程序 native 限制（2026-10-15）

- 新 compute-only isolated 入口，固定 repo／Python、過濾環境、child 握手後才開始 worker；不提供發布旗標。
- Windows 私有 Job Object 設 committed memory／active-process=1／kill-on-close，父程序另管整批 wall time 與輸出；不把 cooperative RSS 當 native memory 的同一量測。
- 追加：hard kill 不等於 job 失敗或成功，更不能逕自解鎖。父程序只證明 owned child 停止，保留 coordinator 的真實 lease／checkpoint 狀態；stderr 不回原文。
- 15 項本地案例實測無限迴圈、大額單次 allocation、native fence 失敗、環境／receipt／輸出與不發布；Linux rlimit 分支未於 Windows 實測，hosted 與整合採用仍待完成。詳見 docs/spec/77-local-process-isolation.md。

### 17.43 第三十九批：全聯盟唯讀一致狀態（2026-10-15）

- 新 mock-only read-only SQLite snapshot 與十三欄 typed view／CLI，分開 desired、config current、最後發布、排刀確認與共用鎖／delivery／configured budget。
- 追加：computed 不能當已發布，最新失敗不能抹掉最後成功 receipt；讀狀態不能順便 migration／recover／改 lease。status_hash 綁定完整快照。
- 十項 mock 驗收含 WAL 真實並行 writer 的一致讀取、一個聯盟壞資料不波及其他聯盟、DB bytes 不變、restore fence、原確認 stale 與 bounded child CLI。
- 最近發布只是 coordinator receipt，不宣稱實際 workbook 回讀完成；完整歷史分頁、遠端 scope 授權與共享表 publisher 仍待完成。詳見 docs/spec/78-read-only-system-status.md。

### 17.44 第四十批：路由與 sender grant 的受授權 outbox（2026-10-15）

- 0010 migration 固定 route head、sender allowlists、管理變更 receipt、batch／delivery binding 與不可逾時釋放的 route lease。
- 同一交易確認固定報告／目前路由／grant；每段 claim 再驗，provider 前以一次性 30 秒 start permit 再驗。撤權可阻止尚未開始的工作，不能召回已發出的許可／請求。
- 追加：新 intent 不得繞過相同報告的 unresolved 舊 delivery；route 改版與 claim 必須互斥，unknown 不能按時間解鎖。receipt 保存後清理中斷只可憑精確正面狀態修復。
- 14 項 registry 與 20 項多段 mock sender 案例通過，包含原子 fanout、撤權、跨 intent、舊 generation、重啟、路由競爭與 stale cleanup；status 只讀出 head／hold metadata。
- 未啟用真實 principal、Discord transport 或 production sender；原低階 LocalOutbox 不自動採用新權限流程。詳見 docs/spec/79-local-routing-and-authorized-outbox.md。

### 17.45 第四十一批：collector 持久分頁與原子觀測（2026-10-15）

- 每頁驗證／白名單後保存 immutable fragment，0011 以 hash／lease-fenced mutable progress 更新進度，本輪結束原子封存完整 manifest 與 raw／training／generation observation。
- 追加：不可每頁留下整份成長 manifest，避免平方級 blob；較早 partial 觀測與其 fragment 也必須有 retention root，不能在 retry 後當孤兒刪除。
- opaque cursor／revision 不落地。partial 重走首頁取得新 cursor；完成 source 只在明確穩定 revision 契約與 fresh head 驗證後沿用，預設全部重抓，不用 auth fallback 舊資料。
- 二十項 mock 案例驗證重啟、revision／scope／短頁、取消、容量、hash／脫敏、歷史保留及 DB 故障原子性；正式 provider 的 revision／resume 語意仍未接線。詳見 docs/spec/80-durable-collection-pages.md。

### 17.46 第四十二批：跨模組合成協作情境（2026-10-15）

- 新建 mock-only 整合入口，串起內容 reference、部分收集／明確 resume、試算表候選、native compute、staging 發布、受授權報告、健檢排名作 raid 輸入、獨立確認、狀態、retention 與 fenced restore。
- 追加：整合證據必須核對來源 ID 串接、未確認草稿不發布、改門檻不重抓、人工 tab 保留，不能只以各模組單測通過推定整段流程可用。
- 四項實際本地情境測試與固定 JSON 證據，十二次 synthetic port calls、三次 native child、兩份 mock 訊息；兩個新 workspace 的資料與計算版本可重現。
- 僅一聯盟／兩人／一角色，不宣稱所有規模、roster、平台、正式 provider 或 A01–A22 完整驗收。詳見 docs/spec/81-integrated-mock-scenario.md。

### 17.47 第四十三批：名單生命週期與加入版本（2026-10-15）

- 固定觀測與確認計畫處理加入、離開、改名及排序；只允許 complete 名單移除成員，人工欄按 composite ID 保留，離開資料完整封存。
- 追加：相同 ID 重新加入仍是新的加入版本，不可沿用先前完整養成或自動恢復人工修正。raw／training／collector／reference 投影均傳遞加入版本，健檢與沿用 planner 必須核對。
- 本地案例涵蓋重新加入舊資料等待、mock 補抓後發布、移除成員直接重算及容量防護。原輸入與目標上限 8 MiB、archive 上限 1,000，不自動丟棄歷史。
- 純計畫確認尚非持久 head CAS；inactive 採用、raid stale 依賴、舊格線人工欄遷移仍待完成。詳見 docs/spec/82-roster-lifecycle-and-member-epochs.md。

### 17.48 第四十四批：名單輸入持久 head 與採用 fence（2026-10-15）

- 0012 保存 union-input head、具 base revision 的 roster candidate 及 request／adoption receipt；候選與採用均重建固定計畫，兩個程序競爭只有一個能更新。
- 追加：純計畫確認不足以保護並行採用；head 變更必須使舊工作在 claim／完成／發布失效，且不得越過 unresolved publisher。舊 receipt 重播只回結果，不倒回 head。
- inactive 可持久採用並阻擋綁定名單的計算；最後成功輸出保留但狀態不再標為 current。唯讀狀態表增為十六欄，保留名單 head 與工作符合性。
- 尚未自動排重算／補抓，獨立來源收集不因未綁名單而被取消；raid confirmed eligibility 與 compiler 仍待完成。詳見 docs/spec/83-local-union-input-heads.md。

### 17.49 第四十五批：排刀輸入編譯與來源過期（2026-10-15）

- 新 compiler 從同 code、目前且已發布的健檢重算核對排名，結合固定名單、raid 設定、完整身分對應的 sync／指定刀／history，產生具完整 lineage 的 problem。
- 追加：problem head 尚未更新不能視為舊確認仍有效。名單或來源健檢 desired 變更，確認及排刀工作都必須即時失效；已確認 view 保留原內容。
- observe 時重編譯比對；compiled task 固定全部來源，queued／running／computed 均共用 fence。唯讀狀態沿用 query-only snapshot，不因依賴檢查執行 migration。
- confirmed publisher、raid 設定／人工輸入的獨立採用 head 及正式格線來源仍待完成。詳見 docs/spec/84-raid-input-compiler.md。

### 17.50 第四十六批：已確認排刀的獨立本地發布（2026-10-15）

- 新 publish_raid_plan 明確意圖命令與固定發布候選，獨立於草稿／確認；同 code 的目前 compiler 來源、confirmed head、目的 route 與完整 manifest 必須一致。
- worker compute-only／native child 只產生 computed，顯式 mock publisher 才透過 staging／回讀寫入五個 protected tabs；人工欄與 tab 保留。
- 追加：新確認／problem 採用也須避開 unresolved publisher；回應遺失保留鎖，正面回讀恢復避免重寫。狀態必須分工作類型保存最近發布，排刀成功不能遮住健檢紀錄。
- 十九項 mock 驗證含各階段 stale、native／budget、五 tab、兩種 response loss、恢復與狀態。正式 provider／公開發布路由／免費用量仍未接線。詳見 docs/spec/85-confirmed-raid-publication.md。

### 17.51 第四十七批：排刀來源 bundle 原子採用（2026-10-15）

- 0013 以單一 bundle 固定 raid 設定、人工指定刀／history、路由與來源健檢／名單，候選與 adopted head 分開；base revision CAS、receipt 與 request 同交易保存。
- 追加：設定或指定刀採用後立即使舊 problem／確認／工作失效，不能只等待 observe 新 problem。route-only 改版保留原計算，只阻擋舊路由發布。
- 十六項 mock 案例驗證競爭、回滾、重播不倒回、unknown publisher、舊 legacy bypass、重啟／retention、分階段工作 fence 與唯讀狀態。
- 不自動 observe／計算／確認／發布；GS 人工來源擷取、跨 runtime 採用及正式 principal 仍待完成。詳見 docs/spec/86-local-raid-source-adoption.md。

### 17.52 第四十八批：固定缺口的成員補抓 executor（2026-10-15）

- 補抓輸入綁定 reuse plan、config／raw／reference／名單／epoch／code；只對有缺口的成員重抓 ownership／details／effects，使用既有持久頁面與原子觀測。
- 追加：新組合快照不可把舊成員的時間冒充成這次刷新，也不能 fallback 掩蓋 auth／partial／not_owned。新 raw 保存逐成員來源鏈，原 raw 不改。
- 完成觀測與需求 ready 分開，尚有缺口回完整 followup plan，不擴大原固定工作或自動發布。過期名單可在 fetch 前原子 supersede lease。
- 十六項 mock 案例驗證補抓、重試、來源／加入版本／時間、篡改前拒絕、not_owned、followup、預算／retention 與 stale fence。worker transport／採用後調度及真實 provider 尚未接線。詳見 docs/spec/87-member-scoped-supplementary-collection.md。

### 17.53 第四十九批：worker 固定 mock 收集 transport（2026-10-15）

- immutable mock script 固定 scope／reference、白名單資料列與有界頁面，runtime 才導出 cursor／revision；worker 不載入真實 URL／Session 或 provider。
- 追加：executor 能力篩選必須發生在候選 LIMIT 前。未綁 script 的 collection 保持 queued／attempt=0，不能塞滿前 100 筆而阻擋可執行工作。
- 普通與 supplementary collection 均可由標記 workspace worker／native child 執行，結果仍只有觀測，不自動排健檢或發布。
- 十七項 mock 案例通過；採用後調度、hosted／細粒度／真實 provider 契約仍待完成。詳見 docs/spec/88-mock-collector-worker-transport.md。

### 17.54 第五十批：健檢採用後持久後續調度（2026-10-15）

- 0014 將 code／reference／baseline raw／mock script／config scope 固定為明確 policy，以 CAS／receipt 採用；逐 scope tick 原子處理補抓結果與新工作，不直接 fetch 或發布。
- 追加：cycle 必須包含 head revision，避免回到相同內容時錯誤重播 desired；完整觀測仍缺欄時每 cycle 最多三輪，重啟不能重設。外部 desired owner 變更須回衝突，不能互相搶 head。
- 二十項 mock 案例驗證實際補抓→重算→工作簿發布、門檻改版不重抓、競爭／回滾、auth／inactive／missing fixture、有限輪次、ABA、來源 proof 與唯讀狀態。狀態表增加調度欄，共十八欄。
- 本批為明確本地 tick API，尚未接背景排程或 worker 自動 hook；公平輪巡／hosted／D1／正式 provider 仍待完成。詳見 docs/spec/89-local-healthcheck-followup.md。

### 17.55 第五十一批：還原 fence 先於初始化（2026-10-15）

- 發現遺漏：worker 在 fence 前呼叫的舊 workspace_status 可能初始化 coordinator 並 migration，不能以「尚未領工作」推定還原副本未變。
- 基本 status 改 URI read-only／query_only，只有 jobs 最小 metadata；worker／isolated 先檢查 restore-lock，才驗證／初始化或啟動 child。
- 七項真實 mock SQLite 案例移除 migration receipt／tables，核對 bytes 不變及無 writer／child，含實際 status CLI；既有相關三十五項驗證亦通過。
- 不解除 restore fence、不提升副本；有界 workflow runner 將採用同一順序。詳見 docs/spec/90-restore-fence-before-initialization.md。

### 17.56 第五十二批：有界公平 workflow runner 與 worker hook（2026-10-15）

- 0015 持久 code cursor，單次有界公平選 scope；失敗 scope 隔離、共享計算預算，cursor 只表示選取，不冒充完成。
- worker 預設最多十次 scope 選取，在處理前／後排後續工作；max_jobs 與 compute-only／顯式 mock publish 邊界維持。獨立 CLI 不開背景程序。
- 追加：入口驗證不該遍歷全部歷史工作；computed manifest／typed grid 應在 publisher 前驗證並隔離無效結果，不能堵住其他 scope。已取得 publisher 的錯誤仍保留 unknown／鎖，不得透過 quarantine 解鎖。
- 十八項整合案例涵蓋公平／競爭／預算／code／fence、單次補抓→重算→mock 發布、native 一 job 後續排隊及無效結果隔離。
- 發現待補：永久無法重試 publication 的安全取消必須具完整未改動／已還原證據；目前不能只靠 commit 不存在或 TTL 釋放。其他 hosted／D1／正式來源與歷史清理仍待完成。詳見 docs/spec/91-bounded-workflow-tick-runner.md。

### 17.57 第五十三批：具明確證據的發布安全取消（2026-10-16）

- 0016 固定新 publication 的 write-ahead send phase 與第一個寫入前的全資源生成區基準。可用「從未開始生成區寫入」或「停止全部 writer 後完整核對原基準」兩種證據明確取消。
- 追加：commit 不存在不等於沒寫入；partial／內容未復原／缺基準／舊 phase／錯 generation 或鎖皆拒絕。基準不能在開始送出後補造或 overwrite。
- 取消決定以 immutable plan／receipt／request 與 effective-state view 保存，不冒充成功；原子 fence 舊 generation、job 記失敗、釋放自己的鎖。重播不碰新 publisher，已套用回應遺失仍走成功恢復。
- 十八項 mock 案例含實際八 tab 格線、人工編輯、rollback／鎖／phase／generation、partial、重啟／retention／備份還原。正式 provider 停止證據與自動 rollback 仍未接線。
- 發現待補：MockSheets 的整份 JSON read／modify／save 缺跨程序人工／生成區互斥，須另以並行測試補齊。詳見 docs/spec/92-publication-no-write-abort.md。

### 17.58 第五十四批：Mock 工作簿跨程序互斥（2026-10-16）

- 修正整份 JSON read／modify／save 的人工／生成區競爭，以同程序可重入鎖加 Windows OS byte lock 包住所有公開讀取／修改流程；POSIX 分支尚未實測。
- 追加：atomic replace 不能取代完整 read-modify-write 互斥；sidecar 存在也不能當 writer 存活或停止證據。等待有界，持鎖不因 TTL 過期，程序退出由 OS 釋放。
- 備份 bytes 擷取共用鎖並保留前後一致性檢查，sidecar 不備份；恢復副本原 fence 不變。整份 inputs replacement 不假裝自動合併 stale client snapshots。
- 十四項實際 thread／子程序案例涵蓋雙向人工與發布、不同 cell、累計更新、重入／退出、timeout／budget、備份還原及錯 bounds。正式遠端 adapter／POSIX／rollback 等仍待完成。詳見 docs/spec/93-mock-workbook-process-locks.md。

### 17.59 第五十五批：共享狀態表本地發布（2026-10-16）

- 0017 固定可信本地路由、唯讀狀態 observation 與遞增 occurrence，AUTO 工作發布三張 protected mock tab，inputs／manual tabs 保留。
- 追加：狀態表 owner 必須排除自身；時鐘變化不應反覆寫表，A→B→A 卻必須建立新次序，不能沿用舊成功。內容／路由／code 綁定與 stale 檢查已補齊。
- bounded worker 結尾至多觀測一次，後續 invocation 計算／顯式 mock 發布；unknown 全域鎖不繞過，預算不足整筆觀測回滾。native child 維持 compute-only。
- 固定雙頁各五十 scope 加快照 metadata，保留明確 as-of；不是完整歷史分頁或遠端瞬時 readback。正式 adapter／ACL／歷史清理與其他獨立工作仍待完成。詳見 docs/spec/94-shared-status-mock-publication.md。

### 17.60 第五十六批：已終止 staging 的安全清理（2026-10-16）

- 新增明確 plan／confirm／replay 的有界 staging 清理，固定 workspace、cutoff、完整 inventory 與原 publication／sealed result 證據。每次最多百個 stage；只有無其他 stage 引用的 chunks 才一併移除。
- 追加：未知 activation 即使看到成功 marker 也不能推定可刪；沒有 parent 的 stage／獨立 chunks 保留。所有 writer 明確停止與無 active／unknown coordinator 是前提，不能靠 TTL 或清理解除鎖。
- coordinator 只持鎖不寫，刪除與 receipt 只在同一 staging transaction 原子提交；SQL／budget／CAS 失敗整批回滾。重播不重刪後來新建的相同 ID。
- 二十四項實際 SQLite／CLI 案例驗證共享資料、terminal／unknown、取消、預算／刪除故障、backup／fence。邏輯 bytes 不冒充物理檔縮小；獨立 chunks／archive／history／receipt 清理仍待完成。詳見 docs/spec/95-terminal-staging-retention.md。

### 17.61 第五十七批：凍結 GS 表頭契約與差異（2026-10-16）

- 只讀本地 GS 原始碼，固定來源 head／hash／行範圍與十個真實模板函式；隔離 Node VM 以 synthetic Sheet 取得實際十二欄表頭及合併結果。
- 七項案例核對名單／屬性／縮寫字典、十二欄 metric 身分、標準表群組與 merge，含未知詞條與 literal 名稱。
- 發現差異：Python 練度表採逐項 header，舊 GS 採群組合併；非標準化詞條顯示字典亦有 trim／縮寫差異。已明確分類，不藉此改原 label 或宣稱可原格覆蓋。
- 完整評分表頭／nickname cache、舊人工區與正式盤點仍未完成，保留驗收缺口。詳見 docs/spec/96-frozen-gs-header-contract.md。

### 17.62 第五十八批：原子排刀採用與草稿排程（2026-10-16）

- 明確 is_generate_draft 意圖把來源採用、problem head、desired draft 與 0018 followup 對應放進同一 transaction；預設只採用保持相容。
- 追加：來源採用後另排工作存在 crash window；A→B→A 也不能讓舊 A 工作重獲 current。新 task 綁此次 bundle，重播只回原 followup。
- 既有 observe_problem 共用內部驗證／CAS body，SQL／採用後 budget 中斷整筆回滾；不自動確認或發布，實際 worker 開 publish 也只產生 draft。
- 十一項新案例與三十六項相關舊案例通過，含 native child、route-only 保留確認、restart／retention／backup。健檢更新後 rebase policy、GS 人工格線與正式入口仍待完成。詳見 docs/spec/97-atomic-raid-source-draft-followup.md。

### 17.63 第五十九批：穩定 ID 人工排刀格線（2026-10-16）

- 新增十欄 mock 人工輸入契約：composite member ID、同步器、三個指定刀與五個傷害覆寫；raw grid 與衍生 member inputs 分別封存。
- 追加：舊 GS 依 normalizeLabel 名稱及輸出列 join 有重名／排序風險，不能默默猜 ID。完整名單、canonical ID、固定 columns、formula／history／容量條件皆明確驗證，0／空／null 保留。
- 候選在同一交易保存 raw／derived／problem／bundle，member_grid_id 綁 lineage 並重建驗證；擷取後 Sheet 編輯不改原候選，計算不改人工格線或自動發布。
- 二十六項新案例與相關來源／followup 合計五十三項通過，含 mock Sheet 擷取→採用→worker、rollback／retention。舊名稱遷移、遠端公式／保護與歷史完整性、輪詢 semantic 去重仍待完成。詳見 docs/spec/98-stable-member-raid-input-grid.md。

### 17.64 第六十批：舊人工排刀格線明確遷移（2026-10-16）

- 支援已知 mock A2:B／A3:W 舊範圍，以兩份明確 physical row→MemberKey 對照建立新穩定 ID 輸入；同名、改名與排序不作 join 推測。
- 固定來源 hash、union snapshot、layout、code、mapping links 與完整 target，確認時重建 CAS。只新增新 input，舊資料／生成區／人工備註與 commit 都保留。
- 追加：預覽後人工變動、重播覆蓋新區、錯誤 archive receipt 與無限歷史都須阻擋。workbook process lock 包住完整流程，最多二十 receipt／整本十六 MiB。
- 二十四項 mock 案例涵蓋逐格保留、同名重排、來源／意圖／身分失配、偽造 target、實際 thread 競爭與 archive integrity／capacity。正式身分對照、其他舊模板、未知離隊列及遠端物理遷移仍未完成。詳見 docs/spec/99-explicit-legacy-raid-grid-migration.md。

### 17.65 第六十一批：D1 原子 CAS 離線契約（2026-10-16）

- 核對官方 batch／limits：分開讀改寫不是交易；本地大 snapshot 不能直接放進 D1 單列，後續須 chunk port 與 query budget。引用與界限詳見 spec100。
- 新增獨立七步 prepared batch 契約，request signature、revision CAS、寫後 head／receipt guard、精確 replay 與 outcome unproven 驗證，沒有 Worker／D1 client／遠端接線。
- 追加：CAS 或 receipt 零列不一定是 SQL error，不能在外層看 batch success 就放行；用 SQL CHECK 強制整批拒絕。response loss retry 仍固定原 request，不能倒退新 head。
- 二十九項實際 SQLite batch／競爭測試，僅作 SQL 契約證據；D1 binding／emulator／完整任務、chunks／publisher／quota／auth 仍未完成。本機未取得 Linux runtime，POSIX 仍未實測。詳見 docs/spec/100-d1-atomic-cas-offline-contract.md。

- 同批追加驗收工具遺漏：證據 regex 不接受 d1 檔名，完整回歸因此誠實失敗；已支援數字 node 並新增五項正反測試，失敗 suite 不生成通過證據。

### 17.66 第六十二批：人工來源到排刀發布整合情境（2026-10-16）

- 單一 synthetic CLI 連接舊人工遷移→raw grid→原子採用／draft→native 計算→明確確認／mock 發布→共享狀態→終止 transport 清理→backup／fenced restore。
- 實際二個 bounded child、五張排刀與三張狀態 tab，保留三種工作簿人工資料與既有成功。另次獨立執行保存 actual receipts 至 docs/validation/2026-10-16-connected-raid-scenario.json。
- 追加：已採用工作完成不等於所有人工草稿已同步。證據明列 has_unadopted_human_edit，pending grid 與原 raw ID 分開，後續編輯不被偷偷採用或覆蓋。
- 五項整合案例含實際 CLI、拒絕既有 workspace 與兩階段 child failure；不偽造完成、不抹除前次發布。正式與 hosted／D1 接線、rebase policy 等缺口保留。詳見 docs/spec/101-connected-raid-mock-scenario.md。

### 17.67 第六十三批：實際本地 D1 binding 與有界 SDK 程序樹（2026-10-16）

- 固定 Miniflare／workerd dev dependencies，真實 getD1Database 執行同一 CAS batch，驗證並行／stale／錯誤回滾／舊 request。無帳戶、remote binding、credential 或部署。
- 新增獨立 Windows SDK profile：最多四 processes、512 MiB aggregate memory、deadline／output 有界與 start gate；一般 Python profile 仍只允許單一 child。實測 runtime ready 後 timeout 關閉 SDK child。
- 追加實際差異：D1 exec 不能直接套 SQLite executescript 的 schema 方式；batch replay 也會寫 guards。已補 positive receipt lookup，完整相符時本地 meta 寫入為零，競爭仍走原 batch，不假稱 account 用量完整。
- 十四項 SDK／Windows 案例、七項 pure lookup 與原十五項 process guard 回歸，另實際 CLI 保存 docs/validation/2026-10-16-local-d1-runtime.json。完整 coordinator／chunks／admission／auth／正式平台與 POSIX 仍待完成。詳見 docs/spec/102-local-miniflare-d1-runtime.md。
