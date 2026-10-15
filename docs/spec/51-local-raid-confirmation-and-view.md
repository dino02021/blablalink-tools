# 本地排刀確認與只讀檢視

domain/raid_view.py 建立結構化只讀結果；adapters/local_raid_plans.py 繼承本地狀態庫並使用相同 migration／objects。尚無 Google 或遠端授權入口。

## 結果檢視

- build_raid_view 核對 problem_id，重模擬完整結果並比對 effective／stage／剩血；來源 ID 必須存在於固定來源池。
- 指定刀目標、傷害、slot 與來源鎖不得變更；自動刀傷害重新核對估算。
- 必要指定警告及缺刀警告不能被移除後交付確認。
- 每列固定 MemberKey、顯示名稱、同步、新刀數與五個 BossView；排序為有新刀在前、同步低到高、顯示名稱及穩定成員 ID。
- C 欄語意的新刀數只計本次落地刀，不含歷史；stage=0 不顯示成已出刀。
- 每刀保留來源、真實傷害、億傷害顯示及階段；同王多刀逐行，全部 IV 時階段只顯示一次 IV。
- 全歷史格才標 is_all_history；混合格逐刀保留 is_history，供後續 rich text renderer 使用。
- 億顯示採局部高精度 Decimal 正數 half-up；真實值以字串保留。原始 FixedInputs 不被排序或清除。

## 草稿與確認

- 0003_raid_confirmation.sql 保存 scope 對應的 input head／revision、不可變 draft、confirmation、確認 head 與 request alias。
- observe_problem 以 expected_revision 作 CAS，只更新目前依據，不變動已確認版本。
- save_draft 封存 problem、final 與 view，讀取驗證每份物件 hash。可保存歷史草稿，但 stale 草稿不能形成新確認。
- confirm 要求固定 draft ID、scope、intent、actor、expected_input_revision 與 expected_confirmed_id；同交易驗證目前依據與更新確認 head。
- 有警告須明確 is_accept_warnings=True。這是供將來操作介面的契約，測試只使用合成確認事件。
- 相同 request／intent 重播只回傳原確認，內容或 actor 改動則拒絕；新排名不會默默替換已確認草稿。
- confirmation 讀取固定記錄與固定 view；confirmation_status 另外回傳 is_stale／is_current_confirmation，不改寫確認內容。
- 兩個操作確認相同 base 時只接受一個；舊記錄與其物件仍保留。

## 證據與界線

local_tests/test_raid_confirmation.py 驗證同名成員、歷史／新刀排序、多行 IV、保留原始指定、完整性、警告確認、過期、重播、重啟及兩個連線競爭。

actor 目前由可信本地呼叫者提供，尚無身分驗證或權限登記。確認不隱含建立雲端工作、修改工作簿或發送 Discord。仍需固定路由、正式工作表座標／rich text 呈現與中央 publisher 串接；本層不是正式 Sheets 確認 UI。
