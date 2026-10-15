# 97：排刀來源採用與草稿排程的原子動作

## 動作與相容性

`LocalRaidSources.adopt_raid_sources` 新增嚴格 boolean `is_generate_draft`。預設 false 維持既有「只採用」契約；明確 true 代表同一次固定候選確認包含「採用並產生草稿」意圖。此選擇進入 adoption signature；同 request 不可從只採用改成產生草稿，亦不能藉重播升級舊意圖。

所有操作仍為本地 SQLite／mock 計算，不呼叫 GS、Sheets 或 provider。既有 candidate ID、base bundle／revision、actor、same-code 已成功發布健檢、完整來源重編譯、固定 route 及無 publisher 等限制保留。

## 同一交易中的後續

採用並產生草稿會在原 adoption transaction 中：

1. 採用來源 head 並保存 adoption receipt。
2. 以共同 `_observe_problem` 驗證重編譯結果，更新 raid input head；同 problem 不增加 input revision。
3. 建立 AUTO generate_raid_draft desired 工作，除原 problem dependencies 外，固定 `raid_source_bundle` 為此次候選 ID。
4. 在 0018 raid_source_followups 保存 adoption→job／problem 對應。receipt 查詢回傳固定 followup 身分，重播重新核對 task，不推定它仍為最新。

public observe_problem 與此動作共用同一 transaction body；沒有先提交來源再另排工作造成的 crash window。排程、followup persistence、SQL 或 cooperative budget 中斷時，來源 head、problem head、desired、job、adoption／request 皆一起回滾。候選預覽本身先前保存的不可變物件不因採用失敗被刪除。

## 次序與草稿邊界

新式 followup task 額外驗證目前採用 bundle，避免 A→B→A 時舊 A task 恢復 current。新 A 可以有相同 problem ID，但 bundle 與工作 fingerprint 不同；兩者不混用成功紀錄。沒有新增 bundle pin 的既有手動 draft 契約維持原 dependency checks。

route-only 採用不改 problem 或既有 confirmation 內容／目前性；若明確選擇 generate，仍排新的 batch-bound draft，舊 followup task 失效。這可能重算相同數學問題，換取清楚的採用意圖與恢復邊界；本批沒有宣稱 route-only 計算可免費省略。

worker／native child 沿用 run_raid_job，保存 final／draft／view 後成功，is_publication_required=False。即使 mock worker 開啟 publish，也不自動確認或寫排刀工作簿。確認與 publish_raid_plan 保留獨立的固定確認、warning acknowledgement 與 publisher gate。

精確 replay 只回原 adoption／followup，不 rewind 後來的來源／problem head、不復活舊工作，也不重新採用已過期健檢。followup 遺失或指向不相符 task 時 fail closed。retention／backup 透過持久引用保留 job／problem，restore fence 不變。

## 證據與缺口

`local_tests/test_raid_source_followups.py` 十一項案例覆蓋原子採用／排程、實際 worker 與 native child、不自動確認／發布、source stale／replay、A→B→A、SQL／採用後 budget rollback、choice signature、route-only、重啟／壞對應、retention／backup／restore。原既有來源與 compiler 三十六項相關案例亦通過。

尚未建立 GS 人工格線擷取契約、遠端入口／principal，以及健檢變更後自動重編譯／重新採用來源的 policy。固定候選綁舊健檢時仍會 stale，不會擅自換用新健檢。這些仍列為後續獨立工作或正式環境驗證缺口。
