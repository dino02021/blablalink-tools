# 跨模組的合成協作情境

executors/local/scenario.py 提供 `python -m executors.local.scenario --workspace <new-directory>`。只接受不存在的新目錄，自建 mock marker／SQLite／合成資料，不讀正式工作簿、設定或 provider 憑證，不部署。marker 常數由 domain/mock_workspace.py 共用，舊 demo 的 MARKER 匯出仍相容。

## 實際串接順序

1. 以內容版本建立並明確發布 reference；共用標準工作簿、聯盟輸出與人工 tab 都是本地 MockSheets。
2. MockCollection 第一次發生指定 detail timeout，保留 partial observation；由合成 operator 明確 reroute／resume，第二次收集成功。沒有以改時鐘或睡眠繞過真實排程政策。
3. 試算表標準經原 reconcile_standard 產生固定候選，reuse planner 核對 raw／training／reference。確認後排健康檢查，所有計算都進 native-bounded child，父程序明確作 mock staging／publication。
4. 同名且以等號開頭的兩個成員靠 composite ID 對應，文字保持 literal。第一次標準為 5，之後協作者改成 9 並修改人工備註；未確認前輸出不動，確認後重算且沿用同一 training，不再呼叫 collector。
5. 新固定報告走 route head／sender grant／Authorized outbox／start permit，兩份 MockDiscord 訊息送達；相同確認重播不增加訊息。
6. 從該次 sealed healthcheck result 的 member／element ranks 建立 RaidProblem，固定 health result ID 與 union inputs ID；native child 只產生 draft，另一個明確確認步驟固定五 tab 工作簿視圖。
7. 以唯讀 aggregate status 確認 desired health／raid 完成、共用 publisher 與 sender holds 都已清空；retention 只刪不可達合成物件。
8. SQLite／workbooks／MockDiscord／staging 一起備份到新 archive，還原至新目錄；還原 scope status 相同，restore fence 保留且 worker 拒絕執行。

native child 使用本機 profile：每次 30 秒、512 MiB，明確記錄 elapsed、exit／stopped 與 configured limits；不將上限當成實際 memory peak。檔案 scenario-result.json 與函式回傳是同一份 JSON 結構。

## 固定資料與實測證據

[2026-10-15-integrated-scenario.json](../validation/2026-10-15-integrated-scenario.json) 為實際合成執行結果，包含 reference／raw／training／完整 collection checkpoint／兩個 config／generated view／raid source IDs、三次 child receipt、十二次本地 collector port calls、兩份 mock 訊息、backup ID 與唯讀 scope status。沒有保存或假造正式 provider receipt。

兩次完全獨立的新 workspace 具有相同 data／calculation／semantic job ID；publisher invocation ID 與執行量測屬於各次運行，不要求相同。collection 的本地呼叫數不是 provider billing 量測。

local_tests/test_integrated_scenario.py 四項案例使用整段流程、兩次獨立資料重現、拒絕已存在目錄與真實 CLI，另外檢查 source IDs、100／40 分與 1／2 名、八 tab、人工保留、report 數量、raid 確認及 restored status。

這個情境是單聯盟、兩成員、一角色的端到端整合證據，不能取代 A01–A22 各別邊界、完整規模／故障矩陣或外部環境驗收。HTTP／GS 提示入口、hosted bindings、所有 roster 生命週期、共享狀態表 publisher、完整歷史清理及正式 provider 行為仍需分別完成。
