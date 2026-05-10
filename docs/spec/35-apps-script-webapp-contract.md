# Apps Script Web App 契約

`WEB_APP_URL` 是 `blablalink-tools` 與 `nikke-union-management` Apps Script Web App 之間的穩定整合契約。

規則：

- Apps Script 例行發布必須更新既有 Web App deployment，不建立新的 deployment URL。
- Python 上傳程式必須持續呼叫穩定的 Apps Script `/exec` URL。
- 如果刻意輪替 Apps Script 公開 `/exec` URL，必須在同一次發布中更新 `WEB_APP_URL`，並驗證 Nikke 上傳通知流程。
- 不可以把舊 deployment 回傳 HTTP 200 視為目前試算表自動化已執行；URL 必須指向目前有效的 Web App deployment。
