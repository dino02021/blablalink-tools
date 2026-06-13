# Apps Script Web App 契約

`WEB_APP_URL` 與 `UNION_WEB_APP_URL` 是 `blablalink-tools` 與 `nikke-union-management` Apps Script Web App 之間的穩定整合契約。

規則：

- Apps Script 例行發布必須更新既有 Web App deployment，不建立新的 deployment URL。
- Python 上傳程式必須持續呼叫穩定的 Apps Script `/exec` URL。
- `UNION_WEB_APP_URL` 預設必須與 `WEB_APP_URL` 指向同一個目前有效 deployment；聯盟突擊上傳完成後會透過這個 URL 觸發排刀。
- 如果刻意輪替 Apps Script 公開 `/exec` URL，必須在同一次發布中更新 `WEB_APP_URL`，並驗證 Nikke 上傳通知流程。
- 如果刻意輪替 Apps Script 公開 `/exec` URL，同一次變更也必須更新 `UNION_WEB_APP_URL`，並驗證聯盟突擊上傳後會重新產生排刀。
- 不可以把舊 deployment 回傳 HTTP 200 視為目前試算表自動化已執行；URL 必須指向目前有效的 Web App deployment。

## UnionRaid upload notification

- UnionRaid 上傳後通知 WebApp 時，`spreadsheet_id` 必須是同一個 `union_id` 對應的 `CharacterDetails`/排刀目標試算表。
- UnionRaid 傷害來源試算表只能放在 `damage_spreadsheet_id`，不能拿來當 `spreadsheet_id`，否則 Apps Script 會因為不在聯盟管理表授權清單內回傳 `unauthorized`。
- headless pipeline 在 strict 模式下會把 WebApp 通知失敗視為上傳未完整完成，因此這個 mapping 失敗會阻擋後續自動排刀。
