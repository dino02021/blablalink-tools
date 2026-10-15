# 本地路由 head、sender grant 與發送開始許可

0010 migration 與 LocalRoutingRegistry／LocalAuthorizedOutbox 補上先前固定 route snapshot 以外的「目前路由與目前授權」邊界。全部只用本地 SQLite／MockDiscord；沒有真實 Discord transport、外部 principal 驗證或 send_reports worker 接線。

## 路由與 grant 的固定管理變更

RoutingAdministrator 是可信本地呼叫者建立的 actor／scope 物件，不得直接從遠端 JSON 建立。route snapshot 必須為固定 scope／reference 的 report_routes，最多 1000 個唯一 composite member、2 MiB；target thread 明確，mention 為最多 32 位 ASCII 數字，未知欄與重複成員拒絕。

SenderGrant 固定 scope、principal、明確 allowed_threads／allowed_mentions、is_enabled，allowlist 去重排序後納入 grant_id。最多各 1000 項，沒有萬用授權；enabled grant 不得無 thread。grant 的 principal 是伺服端可信身分，不是使用者可自行選的 sender 名稱。

publish_route 與 publish_sender_grant 都要求對應 scope 的 administrator、明確確認 ID、base route／grant revision、code SHA，並在 SQLite BEGIN IMMEDIATE 中保存 head／grant、不可變 change receipt 與 request 去重。舊請求重播只回原 receipt，不倒回新路由或重新啟用已撤銷 grant。

路由更新前會檢查該 scope 的 unresolved route leases；任何 in-flight／unknown 都阻擋更新。grant 可以先撤銷，讓尚未取得 start permit 的工作停止；撤銷不能召回已取得許可或已送出的請求。

## 從報告確認到每段發送

LocalAuthorizedOutbox 綁定可信 principal，禁止直接 enqueue DeliverySpec 或注入 caller validator。

1. prepare_batch 仍只保存候選，不排訊息。
2. 第一次 confirm_batch 在同一交易使用固定 ReportCandidateValidator 核對已發布／仍 current 的報告與固定 route，再核對目前 route head、sender grant、所有 thread／mention。原確認 grant 與每份 delivery binding 一起保存；fanout 中任一項失敗全部回滾。
3. 不可將舊無授權 outbox 或 legacy accepted batch 事後升級成受授權工作。accepted 重播只回既有 IDs，不重排 parts，也不替換原 principal／報告／route。
4. 每個 queued part 的 claim 重新核對目前 route／grant，原子建立 scope route lease 與 sending generation。此 lease 尚未開始 provider call，只有 30 秒的開始時間窗。
5. send_next 在 provider.send 前呼叫 port 的 before_send（若有）。Authorized port 再核對 scope／generation／目前 grant，原子消耗一次性 start permit。過期、時間早於簽發或權限已撤銷時，未使用許可可證明尚未呼叫 provider：以 sender_not_started 記錄、增加 generation、清掉 lease，回 queued／達 attempt 上限則 failed。
6. 已消耗的 start permit 不可重用，也不因 30 秒過期自動解除未知結果。sent／NotSent／可信 reconciliation 沿用原 durable part 規則；開始許可不是 HTTP exactly-once 的保證。

claim 與 route head 改版競爭時使用相同 SQLite writer serialization：只能是 claim 取得 lease、改版被擋，或改版先完成、舊 claim 被拒絕。沒有持有 SQLite write transaction 等待 provider 回應。

已確認的固定人工發送 intent 不因新 compute desired 而改內容，但每段仍須符合目前 route／grant；不會把舊 payload 改送新討論串。若需新路由，要重新建立及確認新 intent。

## 未明結果與跨 intent 防重

timeout／失聯保存 delivery_uncertain 與 route lease，跨時間／重啟不自動重送或解除路由鎖。仍須舊 sender 停止，加上符合 target／payload 的訊息 receipt 或可信 absence，才可結案／重領。

另外用不含 intent_id 的 delivery semantic hash 固定 report version／member／route／target／parts／mentions。相同固定報告有 unresolved 舊 lease 時，換新 intent 也不能取得相同 delivery 的新 claim。這是 unresolved 防重，不是禁止日後明確確認的人工重送。

sent、provider_not_sent 或 verified_absent 的對應 generation 是正面結案證據。若程式在保存 receipt 後、清理 lease 前中斷，route publication／後續 claim 可以根據這些精確狀態清掉殘留 lease；不能按時間、猜測或缺訊息來釋放。

## 證據與尚未接線範圍

test_routing_registry.py 的 14 項案例驗證固定 head／grant、thread／mention／principal、撤銷與重播、管理 scope、兩連線 CAS、重啟與輸入邊界。test_authorized_outbox.py 的 20 項案例使用真正計算／mock 發布的兩人多段報告，覆蓋 fanout 原子性、逐段撤權、開始許可、unknown／absence／receipt、舊 sender generation、同內容跨 intent、防 legacy 採用、兩連線路由競爭、positive receipt 清理中斷及 status 脫敏。

原 LocalOutbox 仍是低階 mock 測試 port，沒有自動變成受授權 sender；未來外部 handler 必須明確採用 Authorized port，不能繞過 before_send。真實身分／key registry、Discord provider receipt／rate limit、遠端 CAS／權限與 production adoption 仍未完成。
