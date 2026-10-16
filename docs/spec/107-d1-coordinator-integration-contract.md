# 107：D1 coordinator 整合契約（規劃，尚未實作）

## 對齊現有行為

已核對 LocalStateStore.claim／_enqueue／complete_compute／_require_lease 與 TaskSpec。Graph 查詢為 Verify，generation 2026-10-15T23:35:09Z，claim outbound 一層共十二個可見符號、無截斷；coverage 回報 metadata_changed，因此另讀當前 source。0001_state.sql 的 83 行 partial 已讀，為 job_events AUTOINCREMENT，沒有以索引完整性取代 source。

完整 D1 adapter 不能只移植一個 SELECT→UPDATE claim 或四張 snapshot tables。現有語義必須保留：

- exact request replay 先回舊工作，不倒退 desired/source heads；改 request body 必須拒絕。
- fresh enqueue 必須驗證 source heads／發布版本；desired 更新受全域 publisher barrier 限制，既有 succeeded／superseded 工作的復用規則保持一致。
- claim 同時限制 code／executor／command capabilities、retry／attempt、依賴及 current source；原子更新 generation／owner／expiry。
- heartbeat／complete 核對 running、worker、code、generation、未過期；舊 lease 不能覆蓋新 owner。
- complete 只能引用同 D1 已驗證 sealed object，結果不可變；current inputs 已變動則 superseded，不能發布舊結果。
- compute lease 過期可 recover；publisher 或結果不明的外部副作用不能因 TTL 自動釋放。

## 追加整合遺漏

1. 目前 object port、head CAS 比較器採各自 fixture schema。正式架構必須同一 D1 authoritative state，統一 schema/migration ownership 與 workspace schema identity，不能分散成彼此不知道的 DB。
2. 大型 upload／seal 可能超過 lease；未來每個控制 frame 必須綁定授權 principal、job、generation、code／input pins，並有 bounded heartbeat。現有 mock object port 沒有這些欄位，不能直接包成公開 Worker API。
3. 原子 claim 要防止成功回覆遺失後重試又取得第二個工作；成功 claim receipt 固定原 lease。空 queue 的輪詢不能無限制新增 receipts 消耗免費寫入。
4. Receipt replay 不延長 lease；回覆原始已過期 receipt 必須明確分類 stale，不能當作新工作授權。
5. 同一 D1 交易內完成 metadata／head／receipt／lease CAS；大 bytes verification 由 Python 完成，不能將 Python 讀改寫冒充 D1 batch transaction。
6. Object seal 的內容驗證與工作 complete 分開；半成品或孤兒 chunks 不是成功工作，後續 retention 只刪無引用且符合 cutoff 的項目。
7. Free usage admission 必須涵蓋 schema guards、分片讀寫、receipts、heartbeat、重試與其他專案共用帳戶用量，未知不得算零。

## 後續落地順序

統一 D1 schema/session port → 原子 enqueue／claim receipt／lease fencing → sealed result 完成與 checkpoint → dependencies／recovery／admission → publisher/resource barriers 與 outbox → 既有本地情境的 D1 backend parity。

所有開發繼續使用 synthetic snapshots 與本地 Miniflare，不連正式試算表、不部署、不調用正式 provider。此文件列的是下一階段必須實作的契約，不代表 D1 coordinator 已完成。

## 追加：完整 schema migration 也有 invocation 界限

2026-10-16 重新核對 [D1 官方 limits](https://developers.cloudflare.com/d1/platform/limits/)：Free 每 Worker invocation 最多 50 queries；batch 內個別 statement 也受相關界限約束。完整 schema 不能把現有多次 migrations 全部拼成一個無界 bootstrap。

後續 migration runner 必須逐版本、限制每批 statements，保存 checksum／revision 與 readiness barrier；未完成 schema 驗證前禁止 claim／publish。各批原子 rollback 並不等於跨批原子完成，不可用本地 marker 或 process flag 宣告 ready。D1 batch 的交易行為依 [官方 binding 文件](https://developers.cloudflare.com/d1/worker-api/d1-database/) 驗證，仍需本地 interruption／replay 矩陣。

## 追加：checkpoint、回收與本地多 client 驗證

已讀目前 Root save_checkpoint／load_checkpoint／_recover 當前 source；graph search 無截斷，metadata_changed 採 source fallback。既有通用 checkpoint 使用同租約 UPSERT；D1 跨 request 需新增 checkpoint revision CAS，exact replay 回舊 receipt 不倒退較新進度，更新仍綁 generation／sealed object。Training collection 的專用 progress pointer／觀察驗證也須另做 parity，不能以通用 checkpoint 冒充完整 collector 恢復。

Compute expiry 可依 attempt limit 轉 retry_wait／failed；依賴失敗須傳播。D1 回收每輪限制筆數，不能照搬無界 while；分批傳播期間不得錯誤領取依賴未成功的工作。Publisher／外部結果不明的鎖仍不屬於 compute TTL 回收。

目前 D1 並行證據使用同一 Miniflare binding 的 concurrent batches；尚未覆蓋兩個 owned SDK instances 同時開啟同一 persist directory。本地 session 的跨 client 行為需另測並決定是否採 cooperative workspace lock；不將現有並行證據擴稱為多 SDK 程序驗收。

實作進度：frozen enqueue／原子 claim 核心見 [109](109-d1-frozen-compute-claims.md)，heartbeat／sealed completion 見 [110](110-d1-heartbeat-sealed-completion.md)。域別 freshness compiler、完整 Root parity 與其餘整合仍待完成。
