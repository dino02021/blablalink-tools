# 116：Mock D1 實體備份與 fenced restore

## 新增範圍

既有 local_backup 的 state.sqlite 證據不能代表 Miniflare storage。新增獨立 create_mock_d1_backup／restore_mock_d1_backup，只接受 schema digest 相符、標記為 miniflare_only 的本地目錄，不呼叫帳戶 API 或正式 D1。

備份持有 spec 115 的獨占鎖，等待所有合作 SDK 呼叫完成且子程序停止後，複製完整 storage files（包含 WAL／SHM 若存在）及 mock-d1.json。鎖保持到 archive 寫完。來源 manifest 與所有 bytes 再核對一次；不備份 .sdk-access.lock。這是已停止 SDK 的實體一致快照，沒有宣稱應用層所有租約都已終止。

## 有界格式與身分

- ZIP_STORED，payload 合計最多 64 MiB；目錄遍歷最多 1,000 entries，manifest 最多 512 KiB。
- canonical manifest 包含格式版本、mock 標記、storage kind、schema digest，以及每檔大小與 SHA-256；backup_id 是 manifest bytes 的 SHA-256。
- archive 須是來源工作目錄外的新檔，不覆寫既有 archive 或 restore destination。
- restore 先驗證完整 inventory、manifest identity、schema、每檔 bytes，再建立新目錄。拒絕重複／大小寫衝突／父子 file 衝突、traversal、絕對路徑、反斜線、drive／ADS、Windows reserved names 與符號連結來源路徑。
- 僅 storage 下安全相對檔名與固定 marker 可還原，不使用 extractall，不執行 archive 內任何程式。損壞或過量資料在建立 destination 前拒絕。

## Restore fence

新目錄第一份檔案是 restore-lock.json，先於 marker 與任何 payload；中途中斷也保留 fence。所有既有 SDK session／workspace 驗證都會拒絕此 clone，沒有自動解除 fence、重新啟用 writer 或重播舊租約的入口。僅可唯讀鑑識內容，不把實體還原等同可以恢復跨 runtime ownership。

檔案系統外部非合作寫入不在鎖的保證內。D1 與 Root SQLite／mock workbooks 的跨儲存區一致快照、schema migration ledger、global epoch、release／downgrade 和正式切換仍待後續實作，不合併宣稱已完成。

## 驗收

12 項新增案例通過，與 spec 115 及既有 persistent concurrency／persistence 共 26 項通過。實際 pinned Miniflare 寫入 mock row 後，備份／還原 storage；clone 的 SQLite 唯讀 quick_check 通過且 row 完整，SDK 初始化仍因 fence 拒絕，原目錄可繼續讀取。

另外涵蓋 archive 寫入期間 SDK access 被阻擋、corrupt bytes／錯誤 identity／schema／traversal／大小寫／父子／reserved path 拒絕、磁碟中斷先保留 fence、超量與禁止覆寫。完整回歸另由 spec 64 報告記錄。

## 追加檢查：marker 驗證一致性

檢視發現 workspace port 接受語意相同的 JSON marker（例如縮排），而原 restore 採 canonical bytes 比對，造成可正常開啟的 mock workspace 產生不能還原的備份。已用獨立 mock 目錄實際重現，且確認失敗前沒有建立 destination。

完整 suite 1,165 項通過後，已對齊語意／4 KiB 界限並補 round-trip regression，保留原 marker bytes 與 manifest 雜湊；此新增案例另行 focused 驗證，不回填成前一輪完整 suite 已包含的案例。
