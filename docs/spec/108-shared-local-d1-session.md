# 108：共用本地 D1 schema／session

## 行為

D1Statement 統一 prepared SQL wire，複製參數序列，拒絕 bool、浮點及超出 JavaScript 精確範圍的整數。原 ObjectStatement 保持相容別名。

D1Schema 從可信靜態 table/index/trigger declarations 建立 schema identity、完整 DDL 與 owner inventory guard。每個小模組最多 32 declarations，控制封包限制 64 KiB；這不是全專案多版本 migration runner。

Bootstrap 同一 atomic batch：允許全空或完整相符的 pre-guard → IF NOT EXISTS declarations → 完整 post-guard。已有部分 schema 缺損時先拒絕，不能偷偷重建遺失資料表。保持既有 object-only schema ID，不自動改寫 marker 或擴張既有 DB。

LocalD1Session 使用同一 marked persistent directory，所有 batch 先檢查整體 schema。普通 frame 最多 49 業務 statements，加 guard 合計最多 50。多 frame 依序每組最多 32，沿用 512 MiB SDK 限制與 sidecar 完整性。

LocalD1Objects.with_session 明確接收擁有完整 object schema 的 session；既有 object-only 介面保持可用。物件與 receipt-driven head tables 可以在同一 D1 運作，尚未改成預設業務 coordinator。

## 已驗證範圍

十四項 focused tests 已通過：純 SQLite schema pin／額外 trigger、宣告與數值邊界、參數防外部修改；實際本地 D1 封存物件與 head CAS 同 DB、跨 SDK 重啟重播、marker mismatch，以及 partial schema 不自動重建。另追加共用多 frame 回歸，待完整 suite。

這是固定 SQL 的本地 adapter，沒有把 SQL 當公開 API。完整 enqueue／claim／lease／publisher／auth／admission、schema 版本 migration readiness 與全部情境 backend parity 仍未完成。

最新完整回歸：1,096 項通過，22 項本地證據通過，is_production_accepted=false。包含最大 inventory、16 MiB 合法物件、同 DB 共用 session 與原成本比較；先前失敗仍保留上述紀錄，不以重試自動掩蓋。完整 D1 業務 coordinator 尚未完成。

追加整合界限：schema guard 的 DDL binding 與 binary chunk 共用 64 KiB frame；日後完整 schema 增長時須驗證組合大小，不能放寬 control body 來掩蓋。必要時依 checksum/readiness migration 契約設計分層 guard；目前超限先拒絕，不宣稱任意完整 schema 都能傳最大 chunk。
