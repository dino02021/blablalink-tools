# 110：D1 heartbeat 與 sealed result 完成

## 契約

新增 compute_actions，固定 request signature 與歷史 receipt。每個 action batch 先分辨 exact replay／body conflict／新授權，再更新 job、插入 outcome receipt、核對寫後證明並回讀；其中任何 SQL guard 失敗整批回滾。

Heartbeat 以 MAX(既有 expiry, server now + lease_ms) 續約，固定 request replay 不再次延長。新 action 必須符合 running／owner／code／generation／未過期；adapter 回報 heartbeat 成功前再核對目前 lease 仍 active。

Complete 僅接受同一 D1 已 sealed 的 result_id，不在 Worker 壓縮或解碼大型 JSON。既有 result_id 不得改成不同內容。成功清除 owner／expiry，依 publication_required 成為 computed 或 succeeded；source pin 已變動則保留 artifact 並成為 superseded。

Complete 的 exact receipt replay 可在租約清除後回覆歷史成功，不重新執行；heartbeat 的歷史 replay 不代表當前租約仍有效。Publisher 尚未接線，computed 也不代表已發布。

## 追加：claim receipt expiry 與目前續約的區別

Claim receipt 的原始 expiry 必須不變，但 heartbeat 可合法延長同 generation 的 job expiry。Active claim 驗證改為目前 expiry >= receipt expiry 且目前尚未過期，不能要求二者永遠相等；仍須相同 owner／code／generation／running。原 request 重播不會產生新的續約。

## 驗證

純 SQLite 覆蓋續約 replay、sealed completion／publication mode、過期／錯 owner／錯 generation／unsealed 拒絕、head 變更 quarantine，以及 UPDATE 被忽略時不得寫成功 receipt。後者刻意繞過 global schema guard 以單獨驗證寫後條件，並非允許不明 trigger。

實際本地 D1 正在驗證 heartbeat→超過原 claim expiry 後重播仍識別同 lease→sealed result 完成→重啟後 exact complete replay，以及舊 heartbeat 不重新取得授權。結果待完成，不先宣稱完整 suite 通過。

## 尚未完成

Recovery／checkpoint／free admission／desired source compiler／publisher／hosted dispatch／auth 與完整 Root parity 仍須後續實作。Action timestamp 來自可信 coordinator clock，local fixture 可注入時鐘；不是對外 caller 的任意授權時間。正式試算表與 provider 不接線。

Focused 結果：十項 SQLite 與一項實際 SDK，共十一項通過。包含續約後 claim 身分、固定 heartbeat receipt、sealed completion 與跨重啟完成重播；完整 suite 正在執行。

本批完整回歸：1,124 項通過，22 項本地證據通過，is_production_accepted=false。未支援的能力仍明確列出並拒絕，不將核心 SQL 證據當完整 coordinator 驗收。
