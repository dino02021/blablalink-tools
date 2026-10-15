# 薄 GS reconcile 橋接的離線契約

integrations/gs/local_control_bridge.gs 是獨立未部署來源檔，不修改既有 GS 專案、不建立 clasp 設定或安裝 trigger。未設定 BBL_CONTROL_CONFIG 或 enabled 非 true 時停用，不讀 signing key、不送請求。

- onEdit 只檢查設定中的 spreadsheet／tab ID 與輸入範圍交集；其他 workbook、tab、範圍直接忽略，不讀任何儲存格內容或編輯者 email。
- reconcileTimer 使用相同提示契約，供未來補漏事件；本階段只直接呼叫 mock timer 函式，未安裝排程。
- 提示是 reconcile task，family=config_reconcile，固定 scope／code／route snapshot 與新的 intent。資料擷取、標準解析、計算與發布不在 GS 中執行。
- body 與 HMAC envelope 使用 canonical JSON、UTF-8、明確 nonce／timestamp／request ID，對應 Python gate。只允許 HTTPS 固定 /v1/requests URL，關閉 redirect；key 只從 Script Properties 讀取，不進 body／回傳值。
- 只把 202＋accepted＋job ID 視為受理；timeout／無效 receipt 是 acceptance_unknown，非 202 為 not_accepted。不以 HTTP 成功代表候選完成，不盲重試。
- Python gate 新增 reconcile 權限，固定 config_reconcile family，從 sealed reconcile_route 解析 resource 並檢查 principal scope／workbook 白名單；route 必須為同 scope 的 union_inputs，reconcile_kind=structured_healthcheck_standard。
- [控制 HTTP handler](66-offline-control-http.md) 已以直接函式呼叫串接測試；沒有啟動 listener 或公開 endpoint。
- run_standard_reconcile_job 使用 route 內固定 reference／role IDs／function IDs，Python 讀 structured mock source，建立固定候選並封存 candidate ID。候選不自動確認／發布；重複觀測由既有 semantic candidate 去重。
- 本地 worker 支援此第三種命令，預設即只讀 mock 標準與保存候選，無需開啟 --publish-mock。

local_tests/test_gs_bridge.py 的 9 項測試以固定 Node VM 的 mock Utilities／PropertiesService／UrlFetchApp 執行 GS；真實 JS HMAC 由 Python gate 驗證，再由本地 worker 產生候選。涵蓋停用不讀 key、範圍忽略、timer、timeout、錯 receipt，並確認 published config／輸出 workbook 維持原版本。VM 不提供 SpreadsheetApp、require 或 fetch；host UrlFetchApp 是記憶體 mock，不執行網路。

尚無真實 HTTP 端點、正式 Properties／principal registry、觸發器、事件 coalescing、durable GS hint queue 或真實資料 reader。標準修改後建立的是待確認候選，符合草稿／發布分離；自動重算正式結果仍需明確確認的 config fanout。這份檔案不得直接當成已部署可用的完整系統。
