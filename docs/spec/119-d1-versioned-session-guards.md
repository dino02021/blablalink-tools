# 119：本地 D1 versioned session 逐 batch readiness

## 行為

新增 LocalD1VersionedSession，沿用既有 SDK tree、mock workspace lock、object sidecar、控制大小與 metadata 輸出驗證。這是明確選用的本地 adapter，尚未取代預設 session。

session 固定可信 AdditiveMigrationPlan、revision 與 epoch；完整 business schema 加 ledger 的 identity 仍受 marker 核對。新目錄只允許 revision=0／epoch=1，由 spec 118 bootstrap 在一個 atomic batch 建立完整結構及初始歷史。其他 initial pin 在建立目錄前拒絕。

每個一般與多 frame batch 順序固定為：完整 business＋ledger schema guard → ready／revision／schema／epoch／完整 checksum prefix guard → 業務 statements。同一交易內檢查，沒有 constructor-only readiness；保留最多 48 業務 statements，加兩個 guards 合計 50，整個 frame 仍最多 64 KiB。Guard row 的值要求真正整數 1，拒絕 bool 與 malformed envelope。

重新開啟只執行守衛及唯讀 SELECT，不跑 IF NOT EXISTS、不重新 seed history；缺失 DDL、migrating 或舊 epoch 都直接拒絕。輸出驗證要求兩個 guards 的精確成功 row，僅剝除這兩份 metadata 後返回業務結果，單 frame／grouped object frames 共用相同規則。

## 驗收與界線

已驗證純 frame 50 statements 邊界及錯誤 guard row；實際 SDK reopen 的 rows_written=0、begin 後舊／新 session 都不能寫入、sealed object 與 grouped frames 保留同一 readiness。另補缺失 business table 不自動重建的實際 SDK 回歸，結果記錄於本批驗證。

新增缺表鑑識 probe 首輪因 fixture 漏傳 schema_statements 清單而失敗，未更新驗收報告。已核對 runner 的 schema_bootstrap 路徑，修正測試輸入並直接檢查 SDK envelope 成功；同時加強 spec 115 的 runtime fixture，避免只核對子程序停止而漏掉 SDK 初始化錯誤。

business＋ledger 仍受 D1Schema 的 32 declarations 上限，超限會在建立工作目錄前拒絕。完整升級的 maintenance lock ownership、quiescence、marker 更新／中斷恢復尚未接線，因此 session 不提供 migrate／unfence API，也不允許繞過 restore fence。實際 SQL migration tests 的原始 fixture不代表正式 upgrade orchestration 已完成。

最新 focused：本模組 10 項，連同 migration／backup／lock 共 56 項通過；缺表回歸確認原表未重建。完整 suite 執行後才更新 spec 64 數字。

本批完整回歸：1,198 項通過（620.94 秒），22 項本地證據通過，is_production_accepted=false；包含 marker regression、ledger／session 及強化後的 SDK envelope fixture。升級協調契約見 spec 120，尚未實作。
