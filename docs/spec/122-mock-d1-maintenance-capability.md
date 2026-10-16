# 122：Mock D1 限定範圍的維護授權

## 實作

MaintenanceIntent 固定可信 migration plan、base revision／epoch、完整 business＋ledger source／target schema identity，以及 canonical maintenance journal。建立 intent 時檢查全部中間 schema、hold policy 與控制 frame 大小，不在 SDK 啟動後才發現未知業務表。

mock_d1_maintenance_access 取得與一般 SDK 相同的獨占 OS lock，前後都檢查 marker、redirected storage 與 fences；只接受匹配 source 或 target marker，以及完全相符的既有 journal。restore-lock 永遠拒絕。一般 workspace／SDK 存取新增對 maintenance-lock.json 的拒絕，不提供公開 bypass bool。

維護授權物件綁 active context 的物件 identity、目前 thread 與 lock lifetime。只允許 intent 預先建出的 metadata／schema／history／quiescence 讀取 frame，以及 begin／每版本／finish 的精確 payload SHA-256。離開 context、複製／偽造、換 thread、換 directory 或改 SQL body 一律拒絕。

固定 metadata reads 可在 journal 前執行，讓高階流程先驗證 D1；任何 mutating frame 都須先有匹配的 durable journal。fence 以 owned temporary file、flush／fsync 與原子 replace 建立；context 結束不自動刪除 journal，因此未知回覆仍保持 fenced。此物件沒有 restore 解封或 migration 完成的公開入口。

run_d1_maintenance_batch 僅接受 typed bounded statements 與 active capability，重用原有 Windows owned SDK tree／環境白名單／512 MiB／四 processes／deadline。每次啟動前再核對 grant、frame 與 fences；不重入 shared lock、不放寬普通 SDK 的保護。

## 已驗證與缺口

九項維護案例及 spec 121 的十五項共 24 項通過。包含 journal 前只讀／寫入拒絕、journal 後 normal entry 拒絕、thread／複製／body／directory／過期授權、錯 journal 不覆寫、restore 優先、exclusive 排除其他 readers，以及未授權情境確定不啟動子程序。

實際 SDK 於獨占授權內讀 persisted authority，再建立 journal 並執行 begin，核對 migrating 狀態及子程序停止；context 結束後普通 versioned session 仍因 fence 拒絕。D1 phase 判讀、receipt 續跑、原子 marker 替換與最後 journal 移除由後續 spec 123 的協調器處理，本授權模組不單獨提供解封工具。

本批完整回歸：1,252 項通過（774.22 秒），22 項本地證據通過，is_production_accepted=false。完整 Root／正式 provider／大型分層 schema 仍未驗收。
