# 118：D1 additive migration ledger 與逐版本 CAS

## 本批實作

coordinator_schema_authority 保存 revision、schema_id、epoch、ready／migrating、固定 plan 與 target；coordinator_schema_history 保存逐版本 checksum、前後 schema identity 與 applied_epoch。兩個 immutable triggers 禁止覆寫／刪除 history，完整 ledger DDL／owner guard 拒絕額外 trigger。

AdditiveMigrationPlan 固定完整 schema chain，先複製為 tuple。每步保留全部舊 declarations 並新增至少一個 table／index／trigger；checksum 串接前一步 identity，plan_id 固定整條 chain。第一階段最多 16 個 revisions，沒有 ALTER／DROP／資料轉換／降版或任意 SQL 的公開入口。

每一版本單獨 atomic batch：驗證 ledger、固定 plan／epoch／前一 revision、完整歷史 prefix、before schema → 新 declarations → after schema → immutable receipt → authority revision CAS／post-proof。CREATE 不使用 IF NOT EXISTS，因此與未管理既有物件衝突時整批失敗，不偷偷採用它。

begin 在核對 ready 與歷史後只增加一次 epoch 並轉 migrating；finish 需全部 history／最後 schema 都符合才恢復 ready。同一 finish 重播實際 rows_written=0。已提交 step 由 read_revision／verify_revision_receipt 取得 positive evidence，不重播 DDL；直接重播已完成 step 會被前置 revision guard 拒絕。

下一個 release 可以延伸相同 checksum prefix，從既有 ready revision 開始並增加 epoch。所有 frames 預先限制 50 statements／64 KiB，所有 prepared statements 沿用 100 bindings／4 KiB SQL；16 步完整 history guard 使用 68 bindings，不放寬既有界限。

## 驗收

首輪 18 項 SQLite 契約與 3 項實際 Miniflare 案例通過。包含：保留原資料、升級中拒絕 stale write、跨 SDK 停止後依 receipt 續跑、finish 零寫入重播、舊 epoch 拒絕、競爭 begin 只取得一個 epoch，以及同一 step 後段失敗時撤銷前段 CREATE 與 history。

整合 versioned session 時另補：即使最後 physical schema identity 相同，歷史分組順序／checksum chain 不同仍不能接受 ready pin。ready_guard 現在同 statement 核對完整 history prefix，最多 71 bindings；新增此案例，並讓額外 future receipt 同時阻擋一般業務 frame。

另涵蓋缺版本、checksum／plan 改變、錯誤 epoch、未知 ledger trigger、immutable history、不合法／非 additive plan、完整 16 步與 positive receipt 型別／身分。SDK 只用 mock storage，沒有任何帳戶或正式工作簿接線。

## 還沒有完成

這是 spec 117 的結構 SQL 階段，尚無完整升級 orchestrator、排空業務 active／unresolved holds 的 quiescence port、本地 maintenance capability／journal、marker commit 恢復或正式 Worker entry。一般 LocalD1Session 不會自動採用這份 ledger；不能以這份模組宣稱完整 Root migration 或任意大型 schema 都能升級。

正常 frame 的 ready／epoch 接線見 spec 119。Restore clone 的永久 fence 不提供解封；原有 schema cap、完整 D1 業務 adapter、publisher／hosted／admission 仍維持明列缺口。

本批完整回歸：1,198 項通過（620.94 秒），22 項本地證據通過，is_production_accepted=false；包含 marker regression、ledger／session 及強化後的 SDK envelope fixture。升級協調契約見 spec 120，尚未實作。
