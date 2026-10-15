# 95：已終止發布的暫存傳輸副本清理

## 本地契約

`LocalStagingRetention` 只接受已初始化、未 restore-fenced 的 mock workspace 與既有 staging.sqlite；拒絕資料庫 symlink 和超過 64 MiB 的 staging 主檔。入口先驗 fence 才開 writer。它不讀正式資料、不改生成工作簿、不刪 coordinator 的 immutable result、job 或 publication history。

plan 與 apply 都要求可信本地呼叫者明確提供所有 coordinator／staging writer 已停止的事實。時間、lease 失效或看不到程序不構成停止證據。工具同時拒絕任一 publisher／resource lock，以及 running、dispatched、publishing 或 publish_uncertain job；不能藉清理解除未知狀態。

## 候選證據

每個 stage 必須有唯一同 job／commit 的 publication、相同 sealed result，且 effective publication 為 succeeded／aborted、job 為 terminal、updated_at 不晚於 cutoff。activated stage 另要求成功 publication；activation_uncertain 無論 publication marker 都保留。沒有 publication、未知 parent 或 computed／queued parent 不當成垃圾。

stage resource／revision／manifest object ID 必須對應原 sealed result 重建的 workbook_generated 文件。manifest、每個已有 chunk hash 與長度都驗證；sealed／activated／uncertain 另解碼驗證完整內容。壞 descriptor／manifest／chunk 或容量超限整批拒絕，不用「壞了所以可刪」推論。preparing／cancelled 可以保留不完整傳輸的型態。

單次最多選 100 stages，按 terminal 時間再 stage ID 排序。stage 清單、所有 manifests／chunks hash 和相應 terminal evidence 共同形成 inventory hash。固定 plan 包含 workspace 路徑 hash、cutoff、limit、inventory hash、stage／object IDs 與邏輯 chunk bytes，apply 必須確認其精確 plan ID 並重新計算 CAS。

先刪選定 stages；只有沒有任何剩餘 stage 引用的 manifest／chunks 才能一併刪除。與仍需恢復或未證明 parent 的 stage 共用的 chunks 保留；完全沒有 stage 的獨立 chunks 也保留，不能推定它們是同一種暫存垃圾。

## 原子性與界限

先取得 coordinator BEGIN IMMEDIATE，再取得 staging BEGIN IMMEDIATE。coordinator 不寫任何資料，staging deletion 與清理 receipt 放在同一 staging SQLite transaction，因此不依賴兩個資料庫的分散式 commit。例外、計算預算或 SQL 刪除失敗都回滾 staging 資料與 receipt；coordinator 鎖在決定完成前維持。

最多盤點 10,000 stages、10,000 manifests、20,000 chunks；讀 payload 前先檢查總儲存長度，並限制 metadata／compressed bytes 與宣告 decoded raw 總量各至 64 MiB。每個迴圈有 cooperative budget。超過界限先拒絕，不把前半部當成完整 inventory。

receipt 與 plan 留在 staging_retention_receipts，備份會保留。精確 replay 驗證原 plan 與 receipt 後只回原決定，不重刪之後重新建立的同 ID stage。restore 保留 fence，不能直接初始化清理 writer；plan 也綁定原 workspace 路徑。

deleted_chunk_bytes 是本次移除的 payload 邏輯 bytes。SQLite 可重用空頁，本批沒有 VACUUM、檔案壓縮或實體磁碟縮小保證，也沒有遠端 D1 配額回收證據。

## 入口與證據

`python -m executors.local.staging_retention` 提供 plan／apply：workspace、plan 檔與 `--all-mock-writers-stopped` 必填；plan 另指定 cutoff，可設定 1–100 limit；apply 另指定 confirm-plan。plan 檔只以新檔建立，不覆寫。apply 只讀最多 64 KiB plan，採用本地計算預算。不開排程、不自動清理。

`local_tests/test_staging_retention.py` 的 24 項案例涵蓋真實 terminal stage、receipt／history／workbook 保留、replay 不刪新副本、cutoff、四種 shared state、uncertain、停止與確認、CAS、SQL 與刪後 budget rollback、壞 chunk、獨立 chunks、未知 publisher、computed parent、安全取消後 sealed stage、limit、backup／restore fence，以及實際 CLI。

尚未處理獨立 chunks、archive／history／receipt 的生命週期、實體檔縮小、遠端 staging 或正式 writer 停止證據。這些缺口保留於主規格與驗收表。
