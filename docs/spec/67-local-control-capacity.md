# 控制請求的本地容量與 nonce 保留

0008 migration 與 LocalRequestGate 為「新 envelope 入庫」加入持久預算，非 provider 帳戶用量。預設每 principal／UTC 分鐘 60 次、全域／UTC 日 10000 次，可由可信 constructor 調低；不是免費平台額度保證。

- 新 nonce 即使是同 request／body 的 alias，也消耗一次入庫預算，避免以重播製造無上限 nonce rows；同一完整 envelope 的精確重播不再次消耗。
- minute 與 global day 在同 BEGIN IMMEDIATE 交易保留，任一超限則整批回滾，不存 nonce／request 或增加另一個 counter。
- HTTP 回 429 control_capacity_blocked 與 Retry-After；不啟動工作、不自動付費或重試。薄 GS 只顯示未受理，仍由後續 reconciliation 補觀測。
- 儲存 server 秒級 clock watermark；已驗簽請求若 server 時間倒退，停止入庫並回 unavailable，不因時鐘回撥重新接受已過期 envelope。
- 在單調 watermark 下，可清除 signed_at 嚴格早於 now−300 秒的 nonce；其簽章已不可能再次通過時間窗。舊 migration 的 NULL timestamp 保守保留。歷史 request ID／body 去重紀錄不因此刪除。
- 已過 minute／day 的 usage buckets 隨成功交易清理，保留當期計數。沒有背景 cleanup 或 timer。

新增 5 項本地測試驗證 quota 重啟、精確重播不重扣、global 超限回滾 minute、兩連線僅一筆受理、nonce 過期清理／clock 回撥，以及 HTTP 429。其他既有簽章、重播與 crash 測試繼續執行。

這層仍在驗簽與來源授權後才計數，不能取代邊緣流量限制或 D1 實際行數／大小預算；無效請求可能消耗驗證 CPU，長期 request 歷史仍需另訂歸檔。尚未讀取正式用量或接上遠端額度，不能以此宣稱整個系統長期必定不超免費容量。
