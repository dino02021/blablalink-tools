# 已確認排刀的本地發布候選與五 tab 寫入

新增 publish_raid_plan 明確意圖命令。生成草稿、確認計畫與發布工作分開；此命令只供可信本地 coordinator，既有 signed HTTP／GS 入口仍不接受發布命令。

## 固定發布候選

compile_raid_publication 只接受目前 confirmed head、未 stale、同 code 且經 84 compiler 的排刀。固定確認 ID、problem、原 view、healthcheck lineage 及目的 route。route 為 union_inputs subtype raid_publication_route，只有 input_kind、resource、layout_revision，必須同 scope／reference。

候選 hash 綁定完整 manifest，包括五 tab 的 typed raid_workbook_view 與 view_id、confirmation／problem／draft／code、config／training。prepare_raid_publication 必須明確確認 candidate ID 與 intent_id，原子重建候選、保存 immutable candidate、提交固定來源的 job；不直接寫工作簿。相同 request 只取原 job，不能藉重播恢復過期資格；不同 request 仍須重新驗證目前來源。

## 計算與 publisher

worker 支援 publish_raid_plan。compute-only（含 native child）重建候選並比對 sealed candidate／完整 task，保存 computed manifest；合作式 time／memory／operation 預算可停在 capacity_blocked。只有明確 is_publish_enabled 的本地 worker 才會再取得 publisher。

coordinator 在排隊、claim／dispatch、完成與 begin_publication 前核對來源健檢目前性、名單與 compiler 版本、problem head 及 confirmed head。新確認或來源變更後，舊 queued／running／computed 發布均不能取得新寫入資格。

observe 新 problem 與採用新 confirmation 也須等待 unresolved publisher 釐清，避免已送出的舊寫入與新採用交錯。精確確認重播仍只回原 receipt。這些鎖不因時間到期解除。

發布沿用 sealed manifest、global publisher／resource locks、stage／chunks、全內容回讀、commit ID 與不確定狀態恢復。MockSheets 將明確 confirmed_raid 類型的 view 轉成五個 protected generated tabs；拒絕混合 health view、改標 problem／draft／view／code、未標類型的 raid view。人工 inputs 與 manual tabs 原樣保留，版面結構改變仍須既有顯式 grid migration。

回應遺失不能重送猜測；publish_uncertain 保留鎖，只有 trusted reconciler 的 owned writer 已停止及回讀證據才可恢復。已套用但失去回應的情況不再次寫入。這些都是本地 simulator 證據，沒有 Google HTTP 寫入。

## 狀態與歷史

唯讀狀態增加 published_by_family，最多 50 類，每類保存最近成功 receipt。last_published 仍是整個 scope 最近成功紀錄，避免破壞原呼叫；typed 狀態表使用分工作類型紀錄，手動 raid publication 也顯示一列，不遮蔽 healthcheck。目前性仍用同一來源 fence 計算，不把 receipt 宣稱為 live workbook verification。

## 本地驗證與尚缺

local_tests/test_raid_publication.py 的 19 項案例涵蓋固定確認／route、偽造來源、compute-only／native child、實際 stage 寫五 tab、人工區保留、精確重播、queued／running／computed stale、兩種回應遺失、publisher fence、恢復只寫一次、換確認、typed grid 綁定、預算與分工作類型狀態。

尚未接真實 Sheets、公開發布 endpoint／principal、raid route 採用 head、來源人工範圍與實際免費用量。本地 publish 旗標只操作標記 mock workspace；備份還原 fence 仍拒絕 worker 寫入。
