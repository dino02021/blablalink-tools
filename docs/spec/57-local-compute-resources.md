# 本地計算時間與記憶體檢查

- adapters/process_memory.py 讀取目前程序 resident bytes：Windows 使用本機 process memory API，Linux 讀 /proc/self/statm；其他 Unix 使用 getrusage peak 作保守替代。讀取失敗不得當 0。
- local_compute_budget 固定預設 240 秒、預留 10 秒、500000 次檢查、512 MiB 記憶體上限，延用 ComputeBudget。worker 為每個計算工作建立獨立 budget，非整批共享計時。
- ComputeExecutionCheck 提供共用 budget check 與每 15 秒 heartbeat；RaidExecutionCheck 保留相容名稱。
- run_healthcheck_job 可明確注入 budget；worker 預設啟用。健檢成員／角色 coverage、報告 records／角色、評分角色／成員與群組迴圈加入合作式檢查，封存 result 前再檢查。
- ComputeLimit 使工作進入 capacity_blocked，未產生結果不發布。健檢目前沒有中途 stage checkpoint，明確 resume 後從同一不可變輸入重算；排刀沿用完成 stage 的 checkpoint。
- 未提供 budget 的舊純本地呼叫保留行為。context manager 即使中斷也恢復原執行 context，不污染下一工作。

local_tests/test_process_memory.py 在實際本機驗證正值、明確小上限及讀取失敗分類；test_local_worker 的四個不同檢查位置驗證健檢中斷均無 result、無半份 workbook，恢復可成功發布。現有排刀時間／記憶體／heartbeat 測試繼續執行。

這是合作式限制，不能中止單次 JSON 解析、序列化、排序或作業系統阻塞；memory reader 讀取的是整個程序而非個別工作，Unix fallback 為歷史 peak。尚未提供子程序硬性終止、平台實際 deadline 讀取或 hosted billing 證明；不得以此宣稱通過雲端資源驗收。512 MiB 為本地保守設定，非平台額度承諾。
