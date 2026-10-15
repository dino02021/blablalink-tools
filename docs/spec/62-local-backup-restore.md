# Mock 工作區備份與還原

adapters/local_backup.py 與本地 CLI 的 backup／restore 只處理明確 mock marker 的工作區，不讀正式設定或憑證路徑。

- `backup --workspace <mock> --archive <new zip> --code-sha <release>`；archive 必須不存在。marker 檢查不啟動 migration。
- 協調 DB 取得 BEGIN IMMEDIATE，拒絕 running／dispatched／publishing／publish_uncertain、尚有 resource lock、sending／delivery_uncertain。備份期間不容其他 coordinator writer 領取工作。
- SQLite 使用 backup API，不直接複製可能遺漏 WAL 的主檔；允許的 DB 僅 state／staging／messages／chunks.sqlite。workbook 限 hashed JSON 檔名，拒絕逃出工作區的 symlink，捕捉前後清單與內容需一致。
- archive 只含 marker、白名單 DB、mock workbook 與 manifest。manifest 保存各檔大小／SHA-256、release label、完整 migration checksum map；backup_id 是 manifest hash。累計 payload 上限 64 MiB、最多 900 workbook。
- `restore --archive <zip> --workspace <new directory> --backup-id <expected hash> --code-sha <release>`；目的地必須不存在，不覆寫既有工作區。
- 還原前檢查 inventory、重複／越界路徑、大小、manifest ID、release label、目前 migration map、各檔 hash、SQLite quick_check／foreign_key_check 與無 active writer。驗證通過前不建立目的工作區。
- 僅寫明確白名單路徑，不使用 extractall。先建立 restore-lock.json，最後才寫 mock marker；worker 遇 restore lock 一律拒絕，status 可讀。未提供解除 fence 或自動啟動功能。
- 成功送出的 outbox 分段與 mock provider message DB 一起保留；還原不重設已送狀態、不重送 ping。

local_tests/test_local_backup.py 的 8 項流程涵蓋完整資料／訊息保存、worker fence、active compute／unknown delivery 阻擋、錯誤 release／manifest／內容／path、不能覆寫，以及實際 scrubbed-env backup→restore→work 拒絕 CLI。

目前 release 是明確標籤與 migration 相容性證據，未封裝或驗證程式 binary；也沒有正式跨 release 自動降版 migration。來源 workbook 的人工輸入為逐檔一致擷取，不宣稱所有人同時編輯時的跨 workbook 原子快照。fence 由本地 worker 遵守，不能阻止可信程式直接呼叫低階 adapter，亦非遠端 executor fencing。正式提升還原副本前仍需證明原 writer 全部停止、release 相容與控制平面唯一 ownership；本階段不執行提升。

90 修正入口順序：restore fence 在任何 writer／child 前拒絕；基本 workspace_status 已改真正唯讀，不會在檢查 fence 時偷偷 migration。
