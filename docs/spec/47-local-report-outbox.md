# 本地報告 outbox

application/delivery.py 與 adapters/local_outbox.py 提供離線發送狀態。adapters/mock_discord.py 只將合成訊息存於本地 SQLite，不含 Discord URL、憑證或網路 client。

## 固定意圖

- DeliverySpec 固定 intent_id、report_version、member_id、route_version、target_thread、所有訊息分段與 mention user IDs。
- outbox_id 是完整 canonical JSON hash；enqueue 要求確認該 outbox_id，不接受確認後替換內容或路由。
- request_id 重播與 alias 都持久去重；相同 intent 只能一份 outbox。人工另行確認新 intent 可以再次發同一報告。
- 每段要求 1..2000 UTF-16 units；caller 需預先提供有效分段。
- allowed_mentions 關閉 parse、roles 與 replied_user，只在第一段允許明確指定使用者；內容包含 @everyone 也不授權 ping。

## 發送狀態

- 0002_outbox.sql 追加表與 message ID 唯一索引，沿用本地狀態庫 migration checksum。
- claim 以交易鎖定第一個未 sent 分段，先前分段 sending／unknown／failed 時後續不得超前。
- 有效 worker／generation 才可 sent、reject、uncertain；沒有到期自動重領 sending。
- provider 明確未送出才可 reject，依 retry_after 等待，最多三次。send_next 不自動重試 timeout。
- 可能已送出時 delivery_uncertain；程序消失可 recover_interrupted 隔離 sending，但不授予重送權。
- reconcile 必須由可信核對者確認舊 sender 停止，並提供相符 target／完整 payload／message ID，或可驗證未送出證據。
- 已找到訊息直接記 sent；確認未送出才重新排隊並提升 generation。時間經過本身不是未送出證據。
- spec 每次讀取驗證 hash；不以一個成功 HTTP 呼叫代表所有分段成功。

## 測試與界線

local_tests/test_outbox.py 使用合成 IDs、本地 provider 與時鐘，測試回應遺失、重啟、同 request／新 intent、延遲與次數上限、mention、錯誤 target、兩連線競爭與損毀阻擋。

目前 trusted reconcile 證據由測試呼叫者提供，未驗證真實 Discord 訊息或 run。DeliverySpec 尚未由已發布路由／ReportDraft 自動編譯；報告完整性、格式保留分段、路由授權、真實 rate limit adapter 與雲端 outbox 仍待串接。
