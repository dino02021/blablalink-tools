# 排刀設定、人工輸入與路由的原子採用

LocalRaidSources 與 0013 migration 保存 raid source bundle 候選、目前 head、採用 receipt 與 request 去重。只使用本地 immutable 文件及 SQLite；不建立正式 GS trigger 或外部調度。

## 候選與確認

create_raid_source_candidate 固定目前已發布且同 code 的健檢、union_inputs、raid settings、完整 member inputs 與 publication route，以 84 compiler 驗證完整問題，保存 problem 及 raid_source_bundle。bundle 固定 base bundle ID／revision、全部來源 ID、來源健檢 result、problem 與 code。

建立候選不改 head／problem head／confirmation，不代表已採用人工編輯。compiler 可以驗證候選的新設定；真正 observe／執行／確認／發布仍核對目前採用來源。

adopt_raid_sources 需要 candidate ID 的明確確認、actor、code 與 request。單一 BEGIN IMMEDIATE 交易內驗證 base head／revision、來源健檢仍目前、重編譯同一 problem、固定 route 及無 unresolved publisher，再一起替換整份 bundle、寫 receipt／request。設定、指定刀與路由不會各自採用一半。

兩個程序提出相同 base 的不同候選只能採用一份；DB 錯誤全部回滾。相同採用意圖重播回原 receipt，不能倒回較新 head。actor 仍為可信本地參數，尚未接外部身分服務。

## 失效範圍

settings／member inputs／綁定名單或 code 改變後，舊 compiled problem 的 dependency current 立即為 false，即使 raid problem head 尚未更新。舊確認保留原 view 並標 stale；queued／running／computed 草稿不能取得目前結果或 publisher。既有 legacy problem 不能繞過已採用的 source head。

只換 publication route 時，計算來源未改，原 problem／confirmation 保持有效；舊 route 的 queued／computed publication 失效，新發布候選必須使用目前 route。所有採用仍受 publisher 鎖保護，不能繞過 publish_uncertain。

source bundle 採用不自動 observe 新 problem、不自動生成草稿／確認／發布。後續明確 observe 與工作都必須使用採用 head 的來源。未採用 source head 的舊 mock 情境保留原固定輸入契約，不代表已驗證正式人工來源同步。

## 狀態與保留

唯讀 scope status 提供 raid_source_head 與 bundle／revision／來源 IDs，沿用原 query-only snapshot，無 migration／寫入。既有 16 欄狀態表未新增來源欄；JSON 狀態保存完整 metadata。

head、候選、receipt 及其 problem／settings／member inputs／route 均保留在現有 retention roots／傳遞引用中。備份使用整份 SQLite 與新 migration checksum，不修改既有 migration。

## 本地驗證與尚缺

local_tests/test_raid_sources.py 的 16 項案例涵蓋預覽與採用分離、舊 receipt 不倒回、設定／指定刀立即 stale、route-only 保留計算但擋舊發布、雙連線競爭、來源健檢過期、固定確認／code／request、unknown publisher、交易回滾、legacy bypass、重啟／retention 與三階段工作 fence。

尚缺 GS 人工範圍擷取／穩定 ID 對照、候選後自動排程協作、跨 runtime／D1 採用契約及正式服務 principal。正式試算表與外部環境未接線。
## 後續擴充

97 已新增明確 is_generate_draft 的原子採用／input head／desired draft 排程；預設只採用仍保持原契約，健檢更新後自動重編譯 policy 與 GS 人工擷取尚未接線。
