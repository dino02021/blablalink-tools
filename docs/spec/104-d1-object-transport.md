# 104：D1 物件分片傳輸（開發中）

Python 負責 canonical JSON、壓縮、解壓與完整性驗證；D1 保存跨 invocation 狀態。沿用 32 KiB binary chunks、16 MiB 原始物件上限與 64 KiB 單一控制封包界限。Worker 不執行大型 JSON 轉換。

## 已完成

- encode_value／decode_value 與 Document 共用有界壓縮與雜湊驗證；結果 ID 保持 SHA-256(canonical JSON)，不增加 envelope。
- 解碼重新確認 canonical JSON；重複 key、非 canonical 排序、NaN、無效 UTF-8 即使 hash 正確仍拒絕。
- D1Binary 僅接受 1..32768 bytes，以 canonical base64 加 SHA-256 傳送。Python 與 Node 分別驗證，再以 Uint8Array 綁定 D1 BLOB。
- 實際 Miniflare 驗證 32768 bytes 綁定與錯誤 digest 不寫入。16 MiB 目前只完成 Python codec 驗證，尚非 D1 大物件驗收。

## 待完成

1. 已完成：具 mock marker 的持久 Miniflare workspace 與跨 SDK 程序重啟。
2. 已完成：原子可重播 schema 初始化與失配拒絕，不依賴暫存 initialized flag。
3. 已實作 identity 分離與不可變 statements；不同合法 zlib 編碼的相容性矩陣仍待補。
4. 已完成小型物件續傳、逐片讀回、Python 全量驗證後 seal 與未 seal 拒絕；大型 inventory 分頁仍待補。
5. 大 fixture 使用獨立有界開發 profile，正式 control frame 仍限制 64 KiB；父程序管理輸出並驗證大小與 hash。
6. D1 大物件、跨程序續傳、回覆遺失、損壞與邊界驗收，再接完整 coordinator。目前沒有正式 Worker／auth／account 接線。

## 追加遺漏

大型 bytes 的 pytest 參數須給短 ID，避免測試收集識別字過長；初次收集失敗不算通過。修正後 codec 與既有 chunks 共 40 項通過，實際 binary binding 另 2 項通過；後續完整 suite 結果列於下方。

## 持久儲存與物件 port 進度

已新增獨立 mock D1 workspace，拒絕既有目錄建立、marker/schema 不符、symlink 與 restore fence。這是 Miniflare 的 D1 持久儲存，不是 executor 暫存快照。SDK 每次關閉後由下一個 owned process 重新取得同一 DB。

Schema bootstrap 使用原子 batch；每次物件操作前，在同一 batch 以完整 sqlite_master DDL 與 table/index/trigger inventory guard 拒絕不相容結構。Marker 僅限制本地目錄，不作為業務 schema 正確的證明。

LocalD1Objects 提供 begin／missing／put／seal／read。transport_id 由完整 manifest 計算，object_id 由原始 canonical JSON 計算；每個 object 最多四個 transport。所有 writes 均為 insert-if-absent，seal 僅改狀態，head 保留首個已驗證 transport。相同 chunk replay 必須從 D1 讀回 bytes 核對。

Seal 由 Python 逐片讀回，核對 wire digest、manifest digest、大小、壓縮完整串流與 canonical JSON，再以 metadata 條件 seal；SQL 的 chunk count 不是內容證明。此 trusted Python port 尚未對外提供 API，未具備遠端主體、lease／fence 或防惡意 executor 的授權契約。後續接 Worker 時必須補齊，不能把 unkeyed proof 當授權。

單次 invocation 含 schema guard 與完整 payload 仍限制 64 KiB，單片讀回使用 binary wire 避免 D1 byte array 放大 stdout。現在逐片啟動 owned SDK 程序，適合 correctness 驗證；尚未完成大型物件效率、整體 query/write budget 或批次有界會話。不得推論 16 MiB D1 已驗收。

實際案例已確認跨程序保留、缺片拒絕、未 seal 不讀、完整 seal／read、重播及額外 trigger 拒絕；SQLite 同驗證 immutable statements 與 guard。完整 local_tests 已通過 1,056 項，22 項本地證據通過；is_production_accepted 維持 false。

追加檢查發現：最多 512 片的 inventory 與 manifest 合併回覆可能超過 64 KiB stdout，即使單片合規仍需分頁。後續需分離 manifest 查詢與 bounded inventory pages，再驗證最大形狀。現況超限會終止並拒絕確認，不可宣稱大型物件已可用。

後續進度：最大 inventory 分頁見 [105](105-d1-inventory-pagination.md)，完整合法 16 MiB 實際本地 D1 與回覆遺失／完整性案例見 [106](106-bounded-d1-object-sessions.md)，同 DB schema/session 組合見 [108](108-shared-local-d1-session.md)。前述待辦中的已完成部分依後續文件更新；完整 coordinator／auth／admission 仍未完成。
