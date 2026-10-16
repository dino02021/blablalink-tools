# 121：D1 升級的工作狀態檢查

新增 migration_quiescence，固定登錄 object／head／compute／checkpoint／recovery 的完整宣告；名稱相同但 DDL 不同也不視為已知模組。未知 table／index／trigger 的 hold 語義拒絕，不將未知資料當 idle。僅合成測試的 mock_* name／owner namespace 採明確 fixture 規則。

compute_jobs 只要仍為 running 就阻擋，包含 local／actions／vercel，以及 expires_at_ms 已過去的 metadata；migration 不呼叫 recover、不清 owner 或租約。contract_barriers 任何 scope／global 的 is_blocked=1 同樣阻擋。queued、retry_wait、computed 與歷史終態本身不代表仍持有 compute 租約。

15 項 SQLite mock tests 通過，包括三種 executor、過期 running、blocked barrier、六種等待／歷史狀態、未知 resource／publisher／outbox、改寫的 compute schema，以及假冒 mock trigger 連到正式模組 owner。這沒有證明未來 publisher／outbox 已有完整 policy；新增模組時必須同批補契約與 active／unknown cases。

檢查在已排空 SDK 的獨占區段執行，由 spec 122 的可信讀取 frame 及 spec 123 的高階協調器使用。已完成 release 的只讀重播不重新套用開始升級的 idle 判斷；尚未完成的升級及 marker 收尾仍需完整 hold proof。

本批完整回歸：1,252 項通過（774.22 秒），22 項本地證據通過，is_production_accepted=false。完整 Root／正式 provider／大型分層 schema 仍未驗收。
