# 109：同一 D1 的 frozen task 與原子 compute claim（核心）

## 已實作

共用 schema/session 加入 compute_jobs／dependencies／source_pins／enqueue receipts／claim receipts 與 claim triggers。TaskSpec 先以既有 object port 完整封存，job 只引用其 request_signature，不重複儲存大型 JSON。descriptor 最大 32 KiB、依賴最多 300、source pins 最多 16。

Fresh enqueue 在同一 prepared batch 核對 sealed spec、依賴存在、提供的 source pins 對應當前 head 的 version／revision／code，再建立 job、links 與 receipt。Exact request replay 優先回舊工作，不重驗當前 head、不改寫 receipt 時間。

Claim 的 INSERT SELECT 在交易內選擇 code/executor 相符、排程時間已到、attempt 尚可、依賴 succeeded、source pins 全部 current 的最高 priority 工作。After trigger 原子更新 owner／generation／expiry／attempt 並核對實際寫入；postcondition 失敗會回滾 receipt。

成功 claim request 的原始 job／generation／expiry 永久固定。回覆遺失後用同 request 重播不能領第二個工作或延長 lease；body 不同會拒絕。Adapter 另查 active job 授權，已過期或不再 active 的歷史 receipt 會 LeaseLost，不重新發放工作。

空 queue 不保存 negative receipt，INSERT SELECT 零列、不寫資料。相同 idle request 後來可以領取新到的工作；這只保證此 compute claim 的業務寫入為零，不代表初始化、schema reads 或整個帳戶用量為零。

## 明確能力界限

目前 frozen core 僅接收 prepare_config／refresh_roster_and_training／refresh_raid_history；AUTO freshness compiler 與 publication／外部副作用指令尚未接線，先明確拒絕。Hosted claim 尚需 dispatch admission，不能透過 local claim 冒充雲端授權。

這不是 LocalStateStore 的可替換完整 adapter。Heartbeat／complete／checkpoint／recovery／admission／desired adoption／publisher 仍待完成。Pure tests 的 dependency success／expired recovery 使用明列的 metadata fixtures，不宣稱已完成對應 API。

Frozen core 對同 fingerprint 的 spec_id／pin signature 採嚴格不可變規則；既有 Root 接受新 request 改 priority 等欄位而沿用初始 spec 的外層語義尚待整合 parity，不能直接以本核心替換 Root。

## 本地證據

首輪十一項 SQLite 契約、三項實際 SDK 案例通過：封存 TaskSpec→enqueue→忽略已提交 claim 回覆→重新開啟後重播，以及同／不同 request 的真實並行競爭。另補三個未支援指令拒絕與實際 idle claim 零寫入驗證，完整回歸待執行。

## 後續整合

同一 D1 的 source pin 是 freshness metadata，不代替業務 compiler 檢查必備 config／membership／route／occurrence。此核心沒有公開 SQL/API、principal 或正式 provider；不得由外部 caller 自行宣告 pins 來繞過 freshness。後續固定控制 API 必須由可信 compiler 建立必備 pins 並驗證 executor 能力／預算。

後續 heartbeat／sealed completion 進度見 [110](110-d1-heartbeat-sealed-completion.md)；其餘能力界限維持明列，不代表已可替換 Root。

補測後十四項 SQLite／三項實際 SDK 合計十七項通過，包含未支援指令拒絕與 idle claim 實際 rows_written=0；不代表完整帳戶用量已掌握。

本批完整回歸：1,124 項通過，22 項本地證據通過，is_production_accepted=false。未支援的能力仍明確列出並拒絕，不將核心 SQL 證據當完整 coordinator 驗收。
