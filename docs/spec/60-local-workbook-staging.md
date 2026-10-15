# 本地 workbook staging

application/workbook_staging.py 與 adapters/local_workbook_staging.py 以既有 bounded chunk transport 暫存完整 generated payload；本地 worker 的 --publish-mock 使用這條路徑。沒有建立 Google 暫存工作表或使用正式 workbook。

- WorkbookStage 固定 resource、layout revision、job／result／commit ID、捕捉時 base commit 與完整 ChunkManifest，descriptor hash 為 stage ID。
- prepare_workbook_stage 必須符合有效 publication claim 與 sealed result resource manifest，並檢查 workbook schema。generated payload 封裝為明確 staging_kind 的本地 Document，沿用 32 KiB 分片與 16 MiB 上限；固定 epoch 是 staging metadata，不是資料觀測時間。
- staging DB 與 coordinator 分開，保存 descriptor、分片與 preparing／sealed／activation_uncertain／activated／cancelled。分片可依 missing 清單續傳；全部驗 hash 後才能 sealed。
- 準備、未完成或取消 stage 都不改現有 generated／人工 inputs；cancelled 不能啟用。人工區在啟用時從最新 mock workbook 保留。
- StagedSheets 仍經 publish_workbooks 的 claim／manifest 驗證；stage 的 job result、target、revision、commit 與完整內容必須相符，不能藉 staging 替換計算結果。
- 啟用前要求 layout 與 base commit 未變。先持久標記 activation_uncertain，再呼叫 MockSheets 的原子 generated replacement，完整 readback 後才 activated。
- before／after timeout 或程序消失不得自動重放 stage；uncertain 也不能 cancel。reconcile_stage 需可信 caller 證明 writer 停止與精確內容回讀，僅核對 stage，不解除 coordinator publisher 鎖。未驗到新 commit 不推定未寫入。
- coordinator 仍是全域與實際 workbook 資源鎖的唯一權限來源；直接 stage adapter 是可信本地 primitive，不能公開成繞過 claim 的寫入 API。

local_tests/test_workbook_staging.py 的 6 項測試涵蓋未齊／取消、完整發布與人工編輯保留、schema 變更、before／after timeout、不能盲重試或解除鎖，以及多分片重啟續傳。原本 worker／publication 故障測試繼續執行。

目前 stage 是完整 JSON payload 暫存，實際啟用仍由 mock 單次原子檔案替換；不能代表 Google Sheets 具備跨 API 原子性。尚無真實 staging tab、逐格格式／merge／圖片／保護、遠端清理或 staging 期間的 hosted deadline。此版本在取得 publication claim 後準備 chunks，仍會佔用全域 publisher；正式實作須評估把大量準備移至取得鎖之前，並在啟用時再次驗證 desired versions。
