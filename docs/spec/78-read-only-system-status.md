# 全聯盟的本地唯讀狀態彙總

adapters/local_system_status.py 的 read_system_status 僅接受明確標記的 mock workspace 與指定 scope 清單；`python -m executors.local.status --workspace <mock-directory> --scope <mock-union>` 可輸出 JSON 狀態與 protected ViewTab，沒有開伺服器或回寫試算表。

## 一致且唯讀

- SQLite 以 URI mode=ro、query_only=ON 與單一 BEGIN read transaction 讀取，不經 LocalStateStore constructor，不執行 migration／recover／claim，不更新 lease 或清掉 unknown。
- 同時有其他 writer 時，所有 SQLite 事實維持同一 snapshot；測試以 WAL 下的真實第二連線在讀取途中完成一次寫入，這次讀取仍看原狀態，下次才看新狀態。
- restore fence 只作檔案觀察並顯示，不移除；檔案觀察不宣稱與 SQLite 交易跨資源原子化。
- 同一份狀態的 status_hash 綁定完整輸出，renderer 先驗 hash，拒絕換內容後沿用來源識別。

## 每聯盟與共用資訊

每個 scope 顯示各 family 的 desired job／state／code／executor／固定 versions／result ID／lease expiry，以及該 job 的 config 是否仍為發布 head。歷史 job counts 只作歷史統計，不能把過往失敗當本輪失敗。

另外顯示此聯盟最近一筆 succeeded publication receipt、是否符合該 family 最新 desired；目前這是一筆最近發布，不宣稱列出每個 family 的完整發布歷史。is_live_workbook_verified 固定 false，因為這個查詢不讀回工作簿格線。computed 保持 pending，不當已發布。

排刀 confirmation 以原 problem ID 與目前 input head 判定 stale，和最新健檢是否完成分開。共用區顯示 config／reference heads、publication holds／resource locks、delivery part 計數與 configured usage budgets；共用訊息計數沒有可靠 union linkage，不猜測歸屬。configured budgets 不是 provider 實際 billing。

has_blocked_target／has_pending_target／is_all_desired_completed 是分開的判斷；沒有 desired 不能宣告全部完成。共享 publisher busy／unknown delivery 也在表格各列明示，不把它們錯當某一聯盟的計算失敗。

不回原 spec_json、report body、字典、成員暱稱、憑證或 API response。這仍是可信本機管理者用的 mock 診斷，沒有遠端資料授權接口。

## 有界與隔離

最多 100 個唯一 scope，每 scope 最多 50 desired families；共用 head／hold／lock／budget 各有 101／1000 等明確 row bound，每個 bounded query 文字總量不超過 8 MiB。超限不假裝資料完整。單一 scope spec 損壞或越界回 status_input_invalid，不遮蔽其他 scope；全域 schema 不存在則明確失敗，不自動補建。

application/system_status_view.py 以 composite scope／family row ID 產生十三欄唯讀表，凍結 header 與聯盟／family。顯示最後發布、確認 stale、共用寫入占用／訊息未明與錯誤代碼；這是 typed view，不代表正式 Sheet protection 已套用。

local_tests/test_system_status.py 十項案例涵蓋 DB bytes 不變、失敗隔離、舊發布保留、computed／unknown locks、損壞資料脫敏、restore fence、不 migration、hash／scope 防呆、並行 snapshot、stale 確認及真實 bounded child CLI。完整歷史分頁、遠端 scope 授權與共享狀態表 publisher 尚未接線。
