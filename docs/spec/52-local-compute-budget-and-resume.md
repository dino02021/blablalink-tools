# 本地執行預算與續跑

application/compute_budget.py 與 application/raid_execution.py 提供本地計算生命週期；domain/execution.py 只提供可選檢查 hook，不讀時鐘、平台或憑證。

## 預算

- ComputeBudget 預設 240 秒、預留 10 秒與 500000 次檢查；到達可用期限即中止，不以平台強制終止作正常控制流程。
- 可注入單調時鐘、記憶體用量 reader、記憶體上限。倒退／無效時鐘、未知記憶體讀值與達上限皆拒絕繼續。
- 記憶體 reader 必須回傳非負整數 byte，bool 不算有效讀值；未提供 reader 不可宣稱已啟用記憶體限制。
- Vercel 類型任務額外要求 <=240 秒與 <=1500000000 bytes 且具 reader；沒有觸發遠端 Vercel 的能力。
- 本地金錢／平台用量保留與此執行預算分開；ComputeBudget 不結算雲端帳單。

## 合作式檢查

- ContextVar hook 在退出與例外後必定 reset，不污染下一次計算。
- 候選池、指定準備與完整模擬入口／成員／王迴圈會檢查；重選、改派與交換透過完整模擬持續收到中止訊號。
- application 層在檢查時每 15 秒刷新執行租約；失效 generation 仍由狀態庫拒絕。
- 這是合作式限制，不能攔截 OS OOM、任意第三方 blocking 呼叫或保證每次序列化在預留時間內完成；最差資料大小與實際 RSS reader 仍需獨立驗證。

## 排刀工作與 checkpoint

- run_raid_job 要求 generate_raid_draft、固定 problem_id 版本及相同 code_sha。
- schema_version=1 checkpoint 固定 problem_id、code_sha 與 stage：start、selected、finalized。
- 完成選刀後持久保存 RoundSelection；完成最終整理後再保存 FinalRaidPlan，開始下一階段前不得把半成品標成功。
- 讀取驗證 schema、依賴與支援的演算法版本；最終整理內容須綁定同一 selection_id。
- 續跑可跳過已完成選刀／最終整理。中途預算耗盡只保留上一個完整階段，未完成階段下一次重跑。
- 如果單一階段持續超過短任務預算，應透過中央 reroute 轉較長的相容 executor；目前測試只轉本地，不啟動外部服務。
- 達時間／操作／記憶體限制時回報 capacity_blocked 與安全代碼，result_id 仍為空；不產生正式結果。
- 全部通過才保存本地 draft 與 job succeeded artifact；不自動 observe／確認／發布／發送。舊 desired 已換版時仍依狀態庫標 superseded。

## 證據與界線

local_tests/test_raid_execution.py 使用假時鐘、假記憶體、合成資料與暫存 SQLite，驗證中途期限、容量中止、程序重啟跳過已完成階段、版本拒絕與 hook reset。

尚未實作真實平台 RSS／剩餘期限 adapter、HTTP／遠端 checkpoint transport、Actions／Vercel／CLI 啟動入口與最壞情境 SLA。這些本地案例不能證明雲端時間／記憶體限制已正式生效。
