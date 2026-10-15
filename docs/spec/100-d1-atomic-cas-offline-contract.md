# 100：D1 原子 CAS 的離線契約

## 已核對平台行為

2026-10-16 依 cloudflare 與 workers-best-practices skill 查閱官方文件。D1 batch 會順序執行 statements，任一步 SQL 失敗時整批回滾；不能把分開的讀取與寫入當成 transaction。[D1 Database API](https://developers.cloudflare.com/d1/worker-api/d1-database/)

官方目前列出單 string／BLOB／row 上限 2,000,000 bytes、每 statement 最多 100 bind parameters、Free 每 Worker invocation 最多 50 queries。既有本地 8 MiB snapshot／16 MiB staging 不能直接搬進 D1 objects 單列。[D1 limits](https://developers.cloudflare.com/d1/platform/limits/)

因此後續 remote object port 必須使用有界 chunks／manifest 與逐頁完成證據，並計入每 invocation query budget；現有 32 KiB transport chunks 可以作基礎，尚未實作 D1 object adapter。不能因 SQLite 接受本地 schema 就假定 D1 全部相容。

## 本批內容與隔離

`coordination/d1_cas_contract.py` 產生七個固定 prepared statements 與 positional bindings，沒有 Worker entry、環境 binding、REST client、token、HTTP SQL 入口或部署設定。`coordination/d1_contracts/0001_cas_contract.sql` 是獨立測試 schema，不在 local_migrations 中，也不是可直接套到正式協調器的 migration。

contract_heads／receipts／barriers／cas_guards 只驗證最小 head revision CAS 與 request replay 的 SQL 契約；未接 jobs、union inputs、publisher、route grants、outbox 或 quota。barriers 是合成測試的 scoped／global gate，不冒充既有真正 publisher gate，也不能當正式權限來源。

HeadCasIntent 固定 request、scope、family、expected revision、64-hex version、code 與 actor。signature 綁全部意圖，identifier UTF-8 最多 128 bytes、revision 為 bounded integer。此 profile 每 statement 最多 4096 SQL bytes／100 parameters、text binding 最多 4096 bytes、整批 64 KiB；不接受 implicit string 參數序列、boolean 或不精確整數／大型 payload。

## 七步原子決定

1. 先插 cas guard：相同 request 已有正確 signature 則允許回原決定；新 request 必須 barrier 未擋且目前 revision 等於 expected。條件不符以 CHECK 失敗中止整批，而非零列更新後繼續。
2. 新 request 才有條件 insert／update head，revision 增一；重播不動 head。
3. SQL guard 核對 head 寫入後完整 revision／version／code，防止寫入被忽略仍繼續。
4. 保存不可變 request receipt；衝突不覆寫原紀錄。
5. SQL guard 再核對 receipt 的全部意圖欄位，防止 receipt 零列／錯內容仍提交 head。
6. 查詢原 receipt。
7. 移除同次暫存 guard。任何一步失敗，head／receipt／guard 一起回滾。

精確舊 request 在新 head 或 barrier 出現後仍只回原 receipt，不 rewind、改 actor 或重新 CAS。變更相同 request 的任何意圖欄位須失敗。沒有 receipt 的舊 revision 不能藉 retry 成為成功。

102 的實際 Miniflare 證據補正：這個 batch 的重播仍會 insert／update／delete 暫存 guard，不能稱為整個 DB 的只讀或零寫入。已另提供 positive receipt lookup 契約，只有完整匹配的既存 receipt 才能只讀返回；查無資料仍走原 batch，競爭時 batch 重播成本不會消失。

verify_head_cas_batch_result 只接受七項 success=true 與精確 receipt；canonical JSON 比對區分 boolean 與 numeric revision。response loss、缺項或錯內容都視為 outcome unproven，必須以原 request／原 signature 恢復確認，不能改 ID 重送或當作 rejection。未提供自動 HTTP retry。

## 本地證據與後續

`local_tests/test_d1_cas_contract.py` 二十九項測試以真實 SQLite transaction 執行同一批 SQL，包括每一步注入失敗、head／receipt silent no-op、舊 request 不倒退、scoped／global barrier、兩連線相同 request／相同 base 競爭、回應型別與缺失、參數化字串及資料容量。這是 SQL 契約證據，不是 D1／Workers runtime 驗收。

仍須實作並本地驗證：D1 binding port／emulator、migration checksum 與 root schema 相容性、chunk object port、job／lease／publisher 原子批次、account quota、主體驗證、release binding 與 HTTP 接線。現有 LocalStateStore 未改用此契約。

完整回歸另發現 acceptance matrix 原本只允許英文字母／底線檔名，無法引用 test_d1_cas_contract.py。已允許 lower-case 數字 module／function ID，仍要求完整 local_tests 路徑與 fullmatch；五項回歸案例保留 traversal、參數化 node 與多餘尾碼拒絕。失敗那次 suite 不生成新的通過報告。

另本機 wsl 列表查詢未取得可用 Linux runtime；沒有安裝或修改系統，POSIX process／lock 實測仍保留為缺口，不用 mock 分支取代實測。
