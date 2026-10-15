# 控制 HTTP handler 的離線契約

application/control_http.py 只提供函式式 handler，不啟動 socket、listener、公開服務、Worker 或正式 key registry。預設 is_enabled=False，任何請求回 503 control_disabled，且不接觸 gate。

- 唯一入口 POST /v1/requests，額外 query／不同 path 為 404，不同 method 為 405。
- body 限 1..65536 bytes，超限 413；headers 最多 64 個，拒絕大小寫重複 header，key／value 長度有界。Content-Type 必須 application/json，X-Control-Envelope UTF-8 上限 4096 bytes，JSON 不接受重複 key。
- handler 不信任 envelope 內容，交由 LocalRequestGate 驗簽、重放、命令／scope／resource 與固定 family；只有 submit_signed 回傳已持久保存的 job 後回 202 accepted＋job_id＋job_state。
- 403 request_denied、409 request_conflict、400 request_invalid、503 control_unavailable 皆不回原始錯誤、body、金鑰或資料庫內容；回覆 Cache-Control:no-store。
- 202 不代表計算／候選／發布成功。背景執行仍是另一個明確 worker invocation，handler 不自行重算、重試、發送或部署。

local_tests/test_control_http.py 的 11 項測試涵蓋停用零 gate 呼叫、route／method／body／header 拒絕、錯誤脫敏及 durable receipt。薄 GS 整合測試已改走此 handler，再由 Python gate 驗 HMAC 與 worker 建候選，沒有使用網路。

尚未實作框架／平台 request binding、實際 socket 邊界、TLS、節流、D1 adapter、job 狀態讀取端點或 hosted deployment；此 Python handler 是跨語言可驗證的邏輯契約，不能宣稱 Cloudflare Worker 已實作或可直接部署。
