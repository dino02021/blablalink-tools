# Mock 格線遷移預覽、確認與封存

MockSheets.plan_grid_migration 只讀取現有 mock 工作簿與目標 sealed generated payload，回傳 GridMigrationPlan。適用於已存在的 typed generated_grid；舊十一欄草稿走 [72 的獨立遷移](72-legacy-standard-migration.md)，需要明確 ID／merge／note 證據，不能假裝已有可信身分。

## 固定計畫

plan_id 是完整計畫的 canonical JSON SHA-256，固定 resource、工作簿 layout_revision、base commit／payload hash／grid hash、target payload／grid hash／view_id、config_id／code_sha。changes 列出穩定 tab_key 的 add／archive／replace；替換 tab 的完整目標結構由 target grid hash 綁定。

預覽要求既有 payload hash 與實體格線仍符合來源 view，破損格線不得當正常遷移基底；無結構改變不建立 migration。title 忽略大小寫重複也拒絕。

## 確認與套用

approve_grid_migration 要求 confirmed_plan_id、config_id、code_sha，並從目前工作簿與目標 payload 重建計畫逐項相等，拒絕偽造 changes 或換結果。它只保存待用核准，不寫結果、不建 archive。

一般 apply 發現結構不同時，再重建計畫核對核准；若通過，於原本單次檔案替換中封存舊 generated／grid／commit／payload hash 與 plan_id，寫入新結果並消耗核准。之後若有新基底 commit、payload、格線或 schema，舊核准不可沿用。普通同結構回寫也清掉待用核准，避免遺留授權。

人工 tab 編輯不自動取消核准，但套用前仍核對所有 key／title 衝突。inputs／manual_tabs 不在產生區遷移範圍。每本最多 20 份格線 archive，達上限停止新遷移，不能自動刪歷史；後續需設計有明確保留根的清理。

publication 仍須通過原有 sealed manifest／claim 驗證；核准不是發布權限。回應遺失仍進 publish_uncertain，可信 readback 比對後才解除；before fault 不消耗核准或建立 archive，after fault 重播不重複封存。

## 本地證據與限制

local_tests/test_grid_migration.py 包含預覽／核准不改結果、人工保留、依賴與偽造計畫拒絕、核准後五種變動、before／after timeout、容量限制、刪 tab archive、publisher 與 uncertain reconciliation。確認由可信本地呼叫者提供；沒有用戶登入／遠端簽章權限。沒有操作正式工作簿，也沒有承諾 Google Sheets 多請求具相同原子性。
