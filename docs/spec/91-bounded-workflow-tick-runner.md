# 有界且公平的本地 workflow tick runner

89 的逐 scope API 現已接到標記 mock 工作區的 worker，另提供 `python -m executors.local.workflow_ticks --workspace <mock> --code-sha <release> --max-scopes <1..100>`，單次執行、不開 daemon、不呼叫 provider 或發布。

## 公平選取與預算

0015 保存每個 code 的 round-robin scope cursor；最多掃描 100 個配置 scope，與 policy code 相符者才執行，壞 policy 則列為安全錯誤，不阻擋其他 scope。單次取 scope 與 cursor 推進同交易，兩個程序不從相同 cursor 各自取回同一批。

cursor 在真正 tick 前先推進；程序中斷或預算耗盡時，尚未執行的 scope 仍由持久 policy／state 保留，下輪繞回，不把 cursor 當完成證據。沒有符合 scope 不建立空 cursor；歷史 code cursor 上限 1,000，後續清理契約仍待完成。

獨立 runner 共用一份合作式 time／memory／operation budget。每個 scope 的 tick 仍原子回滾；一個壞 scope 只輸出 workflow_input_invalid，不印來源／錯誤內文。預算耗盡明列 is_budget_limited，不假裝完成其餘 scope。

## worker 接線

存在明確 configured workflow policy 才會排後續工作。worker 在領工作前及每個已處理工作後執行 tick；同次最多 max_workflow_checks（預設 10，可設 0 關閉）個 scope 選取，且共用同一份 tick budget。max_jobs 仍獨立限制實際處理工作數，tick 可能排入之後才處理的工作。

單 scope、預算足夠時，同次 worker 可補抓→排健檢→計算；compute-only 不寫工作簿。顯式 mock publish 才接續 staging／回讀。native child 仍預設只處理一個 job，但可排後續 queued 工作，不能因此自動發布。沒有建立系統排程或正式事件服務。

worker result 增加每 scope 最後一次 workflow_ticks、workflow_scopes_selected、is_workflow_budget_limited 與安全 workflow_error_code；不是每個 queued 工作都已完成的宣告。

## 驗證不順便遍歷／修改歷史

adapters/mock_workspace.py 共用檔案層 marker／fence gate，writer／child 在 marker 與 DB 初始化前先拒絕 restore-lock，不查所有歷史 jobs。基本 status 另以 read-only 查詢 jobs，保留 90 契約。

mock publisher 的 computed 待處理清單改為依 code／local／支援命令與 LIMIT 的 SQL，不先載入整個 job 歷史。新 tick runner 同樣不靠全量 list_jobs 作入口驗證。

## 無效 computed 結果

manifest shape 與 typed grid 在取得 publisher 前驗證。已 computed、尚無 unresolved publication 的無效結果可標 worker_result_invalid／failed，保留原 immutable result 供追查，讓其他 scope 繼續。若另一 publisher 已先領走，reject_computed_result 不改其狀態、不解除鎖。

取得 publisher 後的型別／資料／I/O 錯誤一律保留 publish_uncertain 與既有正面恢復要求，不能用 quarantine 假裝沒有寫入。永久不可重試且已取得 publisher 的工作，目前仍缺具完整「未改動／已還原」證據的安全取消協定；這項遺漏須後續補規格與本地實作，不能只按時間釋放。

## 本地證據與尚缺

local_tests/test_workflow_tick_runner.py 的 18 項案例涵蓋自動補抓→計算／顯式 mock 發布、獨立工作／scope 上限、跨重啟公平輪巡、雙連線 cursor、壞 scope 隔離、budget 中斷後重訪、code affinity、restore fence、實際有界 CLI、native 一個 job／後續排隊、錯 bounds、不合法 computed／typed grid 隔離及既有 publisher 不被撤銷。

89 的直接 API 測試明確設 max_workflow_checks=0，與本批 hook 整合測試分開驗證。尚需安全取消不可重試 publication、raid 後續調度、hosted／D1、正式 trigger／來源完整性、history／cursor 清理與共享狀態 publisher。沒有接正式試算表或憑證。
