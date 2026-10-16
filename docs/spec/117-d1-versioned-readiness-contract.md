# 117：D1 多版本升級與 readiness 契約（待實作）

## 問題

現有 D1Schema 可原子初始化一組最多 32 個固定宣告，schema marker 不相符即拒絕；不能用它宣稱既有資料庫可升級。spec 115 的 OS 鎖能排空本地合作 SDK，但 hosted invocation 不共享此鎖；spec 116 的還原 fence 也不能作為 D1 全域 readiness。

## 必要契約

1. D1 保存 singleton schema authority 及逐版本 migration history。history 固定 revision、前後 schema identity、可信靜態 steps checksum；authority 保存目前 revision、epoch、ready／migrating 狀態與固定 plan identity。不得以本地 marker、Actions cache 或 process 變數取代。
2. 每個版本各自一個有界 atomic batch，包含 before guard、可信 migration statements、after guard、history 及進度 CAS／post-proof；仍遵守 50 queries、100 bindings／statement 與 64 KiB control frame。跨版本不宣稱整體原子完成。
3. 開始升級先原子取得 plan ownership、增加 epoch 並關閉 readiness。不同 plan／改過 checksum／revision gap／外加 trigger 或半套 DDL 一律拒絕。所有正常業務 frame 同 batch 檢查 ready、固定 schema release 與 epoch；不能只在 constructor 讀一次 readiness。
4. 只允許可信程式內的 migration plan；不公開任意 SQL API，不允許外部輸入更改 ledger。歷史 positive receipt 先查驗，已提交版本直接驗證既有證據，不重播業務 DDL。未知回覆後仍用相同 plan／revision 查驗。
5. 中斷後保持 migrating；以同一完整 checksum chain 續跑。完成全部版本並核對最後 schema／history 才原子 ready。修復不得偷偷重建遺失資料表、降低 schema pin 或放寬控制封包。
6. 本地升級須持有獨占 SDK lock，排空全部舊 SDK。升級專用執行 capability 只在該持有區段有效，綁 directory／plan／thread／active lifetime；不得加公開 bool 開關繞過 fence，不能借用 fenced restore 的目錄。
7. 檔案 marker 與 D1 commit 無法跨儲存區原子完成。獨立 maintenance journal 只作本地恢復索引；D1 為 authoritative。完成 D1 後 marker 更新遺失的情境須可驗證恢復，不能讓舊 schema session 趁空窗寫入。
8. 升級前排除 active／unresolved executors、publisher、resource locks 與 outbox holds。現有 D1 尚無完整 publisher／hosted adapter，不可藉新 epoch 宣稱那些語義已保證。還原 clone 維持永久 fence，這份升級流程不提供 restore 解封。

## 開發與驗收順序

先做有界 SQL ledger／readiness／step CAS 的 SQLite 契約，再接真正 Miniflare 原子 batch 與中斷重啟；後續整合 versioned session、舊 session 拒絕與本地 marker journal。每一階段分開記錄已實作與缺口，未通過前不得取代現有預設 session。

測試至少包括：正常多版本保留資料、相同 plan 重啟、不重播已提交 step、checksum 修改與缺版本拒絕、step 內失敗全部 rollback、跨 step 中斷仍不能領工作、兩個 migrator 競爭、舊 epoch／舊 session 回覆不接受、marker 更新中斷與受保護 restore clone 不可升級。全部只用合成資料。

依 [D1 binding 文件](https://developers.cloudflare.com/d1/worker-api/d1-database/) 驗證單 batch 的交易邊界，依 [D1 limits](https://developers.cloudflare.com/d1/platform/limits/) 約束每輪；不把本地 Miniflare 測試擴稱為正式免費帳戶驗收。
