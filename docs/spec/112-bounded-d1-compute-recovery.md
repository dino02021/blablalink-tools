# 112：D1 本地 compute 有界回收

## 已實作

獨立 recovery schema 保存 request receipt、每筆 recovery item、generation 對應 outcome，並加入 job recovery index。每輪 1..100 筆，可限制 scope；按 updated_at／job_id 穩定選擇。

過期 local running job 在剩餘 attempt 內轉 retry_wait，達上限轉 failed；依賴 failed／superseded 的 queued／retry_wait 工作轉 failed；source revision pin 不符則 superseded。清除 owner／expiry，記錄原因並核對 job 與 outcome 的寫後狀態。

Dependency failure 每輪傳播一層，需後續 tick 繼續，不執行無界 while。未成功的依賴始終阻止 claim。Running hosted jobs 不屬於此 local expiry 回收，publisher／外部結果不明的鎖也不在操作範圍。

非空 tick 保存不可變 receipt/items；同 request 重播不倒退後來取得新 generation 的工作。空 tick 不寫 negative receipt，之後相同 request 可以觀察新到的 expired work。Outcome 只在 generation 相符時顯示為 Job.error_code，新 claim 不沿用上一代錯誤。

## 證據

五項 SQLite 契約與一項實際 SDK 案例合計六項通過：期限邊界、重試上限、分輪 dependency chain、scope／limit、stale source、exact replay、零寫入 idle tick、忽略 UPDATE 的回滾，以及實際 checkpoint 經 recovery 接續與舊 generation 拒絕。

實際完整組合 schema 同時測 32768-byte binary echo，含 schema guard 的同一控制封包仍在 64 KiB 界限；沒有將單表測試擴稱任意完整 schema 都可容納。

## 尚未完成

Hosted dispatch／executor run stop proof、整體 admission、domain compiler、publisher 與完整 Root parity 仍待完成。沒有正式 D1／試算表／provider 接線，尚待本批完整 suite。

最新完整回歸：1,142 項通過，22 項本地證據通過，is_production_accepted=false；包含本模組、完整 D1 組合與 SDK 端口回歸。先前完整 suite 的 SDK 失敗原因與修正見 [114](114-fetch-compatible-local-d1-ports.md)。
