# 102：本地 Miniflare D1 runtime 與有界程序樹

## 從 SQL 契約到本地 binding

延續 100，使用官方 Miniflare 的 getD1Database 取得模擬 binding，實際執行同一套七步 prepared batch。沒有 Cloudflare 帳戶、remote binding、正式 DB、credentials 或部署。[Miniflare D1](https://developers.cloudflare.com/workers/testing/miniflare/storage/d1/)

開發依賴獨立放在 coordination/offline_d1，以 lockfile 固定 Miniflare 4.20260730.0／workerd 1.20260730.1；同時取得最新 workers-types 5.20261015.1 核對 D1Database prepare／batch／exec 型別，未手寫 Env。npm 安裝只下載本地 dev dependencies，未呼叫外部平台管理 API。

這是測試 fixture，不是新部署專案；compatibility date 取 pinned workerd 宣告的 2026-07-30，避免把未支援的新日期冒充已測 runtime。實際部署 date／release binding 仍須另驗證。

## Runtime 揭露的差異

第一個真實模擬器執行拒絕將含註解、多列 CREATE 的 schema 交給 D1 exec；SQLite executescript 的匯入方式不能直接等同 D1。fixture 改將可信靜態測試 schema 去除 line comments、按 semicolon 分段後逐個 prepare().run()，不是接受任意外部 migration script 的 parser。

實際 binding 已驗證 CAS、stale CHECK rollback、response-loss 型舊 request replay、不 rewind 新 head、同 request／同 base 的並行 batch、scope barrier，以及插入中途 SQL error 後 head／receipt／guard 全部回滾。回傳 receipt 由共同 Python verifier 再檢查，並未以模擬器「沒有丟例外」就當成功。

## 免費用量遺漏與補正

實測指出七步 batch 的精確 replay 仍會寫暫存 guards；只有 head／原 receipt 不變，不能稱整個 DB 只讀或零成本。100 的文字與測試名稱已補正。官方 D1 計量包含 INSERT／UPDATE／DELETE，索引更新也可能增加 written rows；不應以一個 head 當成只寫一列來規劃免費預算。[D1 pricing](https://developers.cloudflare.com/d1/platform/pricing/)

新增 build_head_cas_lookup／verify_head_cas_lookup_result：先查 immutable request receipt。只有 success=true、唯一 row 且全部意圖欄位／signature 精確相符才可直接回原決定；查無則明確回 None，仍走原 batch。錯 actor／signature、重複 row、boolean revision 或不完整回應拒絕。

fixture 的 lookup_or_batch 路徑實測 positive lookup 的 rows_written=0；競爭者同時查不到時，仍可能走到 batch replay 並消耗 guard writes。這是正常競爭語意，不宣稱所有 retry 都零寫入。

執行證據保留 new CAS、batch replay、verified lookup 的實際 emulator meta。失敗 batch／schema setup／其他專案用量沒有完整納入，固定 is_complete_account_usage=false；這些數字不能取代正式帳戶配額，也不會自動改付費方案或正式預算。

## 程序與 I/O 邊界

Node／workerd 必須透過 executors/local/emulator_process.py 啟動。這個 Windows-only 開發 profile 先建立 Job Object，再以一用途 start gate 放行 Node 載入 SDK；payload 放在父程序建立的最多 64 KiB 暫存檔，gate 只送六 bytes，避免大 stdin write 阻塞 wall-clock 監督。

最多四個同 Job processes，per-process 及 aggregate Job memory 上限皆 512 MiB，deadline 最多 60 秒、stdout／stderr 各 64 KiB。無 breakaway；結束、失敗、deadline 或 output 超限均關閉 owned Job。預設業務 Python profile 仍只允許一個 child；沒有把一般 executor 擴成可任意 spawn。

環境只保留基本 Windows／temporary／locale 值，不傳 NODE_OPTIONS 或 provider credentials。temp payload 只含固定 mock SQL 與合成值。SDK Worker 透過 outboundService 固定回應拒絕，唯一網路探針被本地攔截；這不是整個 Node process 的 OS firewall 證據。Miniflare 僅使用 ephemeral D1，未提供外部 SQL HTTP 服務。

正常證據要求 dispose 完成且所有記錄到的 SDK children 已退出。deadline 測試確實等 runtime ready／workerd 已啟動後，確認關閉 Job 使 root 與記錄的 SDK PID 結束；PID 僅作唯讀觀察，終止只使用 owned Job handle。另直接向 Windows kernel 查限額，並回歸一般 Python 不能再 spawn 第二個 process。

## 證據與後續

local_tests/test_d1_runtime.py 十四項實際 Miniflare／Windows 測試，另七項 pure lookup 驗證與既有十五項 native guard 回歸。非 Windows 的 SDK profile 案例會 skip，驗收報告必須因此保留 incomplete，不能當 POSIX 已通過。

`python -m executors.local.d1_contract_runtime --output <new-json>` 提供獨立有界 proof，output 不覆寫。實際 CLI 證據：[2026-10-16-local-d1-runtime.json](../validation/2026-10-16-local-d1-runtime.json)，固定 is_production_accepted=false。

仍未完成完整 coordinator D1 adapter、chunk object port、job／lease／publisher batch、account budget admission／auth／remote migration／release binding。現有 LocalStateStore 與正式環境皆未切換；本地 emulator 通過不是 Cloudflare Free 每日用量或正式一致性驗收。
