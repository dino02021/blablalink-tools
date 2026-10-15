# 有界本地 worker

入口：`python -m executors.local work --workspace <mock directory> --code-sha <pinned release>`。

- 僅接受已初始化的 mock marker 與既存 DB；不載入正式設定、憑證、provider 或 URL。預設只計算，`--publish-mock` 才啟用 MockSheets。
- 一次處理 1..100 個工作，預設 10，不自建背景輪詢。worker ID 與 code SHA 明確固定；只領 local／同 code 的 recompute_healthcheck、generate_raid_draft、publish_raid_plan、refresh_roster_and_training、reconcile。
- [還原副本](62-local-backup-restore.md) 若存在 restore-lock.json，一律拒絕領取或發布，避免自行成為第二 writer。
- LocalStateStore.claim 的可選 commands 白名單在 SQL LIMIT 前套用，避免大量不支援任務使可處理工作飢餓；其他 caller 未指定時保留原行為，hosted claim 不接受此本地篩選。
- 啟用 mock 發布時先處理同版本 computed 健檢／已確認排刀發布工作，再領計算工作。發布採 persisted manifest 與原有全域／工作簿鎖，不從草稿或目前標準重新算。
- mock 發布經 [workbook staging](60-local-workbook-staging.md) 驗分片、sealed 與 base commit 後啟用；未知啟用不自動續寫。
- ResourceBusy 保留 computed 待下一次；publish_uncertain 保留鎖與狀態，不自動核對或重寫。程序中斷的既有 recover 規則維持不變。
- 排刀輸入由 task.problem hash 讀取本地封存物件，restore_problem 還原 typed identity／history／fixed inputs／ranks，要求與原 canonical representation 一致。budget 中斷保存 checkpoint，需明確 resume 後重領；完成只建 draft，不確認排刀。
- 已知無效輸入以 worker_input_invalid 結束，不輸出原始錯誤資料；LeaseLost 不讓舊 worker 改新 owner 狀態。未預期程式錯誤保留既有 lease 恢復機制，不假裝成功。

local_tests/test_local_worker.py 包含 compute-only→重啟發布、跳過雲端／異版本／未知命令、105 筆不支援工作前置不飢餓、無效輸入、不明發布隔離、排刀 checkpoint resume、實際 scrubbed-env CLI 與未標記目錄拒絕。

目前支援上述五種命令，reconcile 只準備標準候選，不執行候選確認。collector 只領取綁定 immutable mock script 的工作（88），不能執行真實 provider、遠端 dispatch 或發送。已接上[本地 RSS 與合作式迴圈檢查](57-local-compute-resources.md)，D1／外部 runtime adapter 尚未接線；子程序硬性時間／記憶體限制由 77 的另一個 compute-only 入口提供。

85 補上已確認排刀的獨立發布候選與 mock publisher。77 的 native compute-only 子程序亦適用於此命令，不自動發布；正式 provider 仍未接線。
