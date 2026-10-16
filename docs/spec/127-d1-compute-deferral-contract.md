# 127：D1 持久 defer／resume 契約（待實作）

## 現況與 Root parity

已讀 Root LocalStateStore.defer／resume 當前 source：defer 接受 retry_wait／auth_required／capacity_blocked／waiting_for_data／failed，清除 lease 並保留安全 error code／next attempt；retry_wait 用盡 attempts 則 failed。Resume 只接受 auth_required／capacity_blocked，按剩餘 attempts 轉 queued 或 failed；waiting_for_data 必須新 immutable task。

Graph 為 Verify，generation 2026-10-16T11:08:24Z，路徑 coverage 回報 metadata_changed。defer trace 仍含不符合 Root 當前 source 的 LocalD1Session.execute edge，因此以已讀 source 為準，不由該 edge 推論呼叫關係。

既有 D1 jobs enum 沒有三種特殊等待狀態，不能把純 child 的 pending 當 succeeded 或直接輪詢重做；也不能為了加 enum 默默重建舊表。本階段採 additive metadata 與原子 transition，保留原 jobs／receipts。

## 資料與語義

- 新 deferral head 綁 job、code、generation、state、reason、next attempt、原始 request；新 actions receipts 固定完整 body／signature／outcome。三種特殊等待在 physical retry_wait 上保存明確 semantic state，get_job 依同 generation 一致投影；這是持久資料，不能僅用 Python process 變數。
- retry_wait／failed 也能保存相同 reason，保留 Root attempt／delay 語義。新 generation 或 terminal recovery 不採用舊 deferral 的 reason／state，不能遮蔽 dependency_failed／inputs_superseded。
- Wire delay 採精確整數 milliseconds，最多 86,400,000 ms，對齊 Root 最多一天；拒絕 bool／非整數／overflow。error code 保留 Root 的安全 snake_case、最多 80 字元。
- claim 必須在 SQL eligibility 排除特殊等待，讓較低優先但可工作的 job 仍能被領取。另新增 BEFORE claim trigger 拒絕仍 deferred 的 fresh insert，避免舊 selector 誤領；既有 positive claim replay 仍由原 receipt guard 處理，不增加 negative receipts。
- 新 client 由完整 schema capability 自動選擇含 deferral 的 selector，不提供外部 caller 可關閉的 bypass flag。舊 schema guard 在 owner inventory 改變時拒絕，不讓不認識新表的舊程式寫入。
- 宣告出現 deferral module 的任何一部分但未包含全部表與 claim trigger 時，queue／migration policy 都須拒絕。新模組應在同一 additive revision 原子加入，不能讓缺 trigger 的半套 metadata 被當作舊 schema。

## 原子 action

Defer 固定 request／lease／generation／state／reason／delay，fresh action 需 running、owner／code／generation 相同且未過期；同一 batch 轉 physical state、清 lease、保存 deferral、驗證 postcondition、保存 receipt、再驗證 receipt 存在。任何一步 ignored／failed 都整批 rollback。

Resume 固定 request／job／code／expected generation／current deferral request，僅 auth／capacity 允許，清除本輪 deferral 並轉 queued／failed。這是可信本地 coordinator 的 metadata primitive，與 Root resume 同樣不作 provider principal 授權；後續公開控制 API 必須另驗證真正授權／admission，不能把 receipt 或 sealed object 當 auth proof。

Exact action replay 先核對舊 receipt，不再要求舊 lease 活著，也不重做 transition；改 body 拒絕。較新 generation 已開始時，舊 defer／resume replay 只回原證據與目前狀態，不清除新 owner、不重新暫停。

## 整合與驗收

先完成 SQLite contracts，再接實際 D1：高優先等待工作不阻擋其他工作、空 queue 零寫入、三種等待投影、delay／attempt limit、waiting_for_data 拒絕 resume、錯 body／過期 lease／舊 generation、response loss replay、競爭 defer／resume／claim、checkpoint 保留，以及新 inputs 的 bounded recovery 可 supersede 等待工作。

Migration policy 必須同批登錄新完整 schema／hold 語義；增加三個 declarations 後，完整 24＋ledger4＋deferral3 為 31，仍需驗證 32 KiB frame。未知／不完整 deferral metadata 不能靜默當 idle。大型分層 schema、正式 auth／hosted／admission 與 AUTO compiler 仍另行驗收。

所有測試僅新建 mock storage；本文件是下一段契約，尚未實作。
