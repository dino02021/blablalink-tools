# 還原 fence 前置與基本狀態唯讀

本批修正：worker／isolated worker 原先在 restore-lock 檢查前呼叫 workspace_status，而舊 workspace_status 會初始化 LocalStateStore，可能先執行缺少的 migration，再拒絕 worker。尚未啟動計算不代表資料庫未被修改。

## 現在行為

worker 與 native 入口在 workspace writer 初始化／子程序啟動前先拒絕 restore-lock。workspace_status 改用既有 state.sqlite 的 URI mode=ro 與 PRAGMA query_only=ON，只讀 job_id／state／error_code；不呼叫 LocalStateStore 建構子、不執行 migration／recover，也不建立缺少的資料庫或 table。

marker 仍須完整符合 mock marker，讀取前限制 4 KiB。基本 status 保留原 JSON 介面與工作排序；它不是 78／89 的完整跨聯盟狀態。舊 schema 若有 jobs 欄位可讀基本狀態；連 jobs 都沒有時直接失敗，不自動修復。

restore fence 是可信本地入口的防護，不宣稱能阻止直接使用低階 adapter；原程式唯一 writer／正式提升還原副本仍依 62 的外部證據需求。本批沒有解除 fence 或接正式環境。

## 本地證據

local_tests/test_restore_fence_order.py 的 7 項案例使用真實 mock SQLite，移除 0014 的 migration receipt 與對應 tables，模擬待升級工作區。驗證 worker／native 拒絕前不啟動 writer／child、基本 status 與實際 bounded 子程序 CLI 不 migration、SQLite bytes 不變、缺 schema 不建表。

既有 worker／process guard／backup 相關 35 項亦通過。後續 workflow tick runner 必須沿用這個前置順序，不能藉驗證工作區而先寫還原副本。
