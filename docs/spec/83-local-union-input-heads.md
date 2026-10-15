# 本地名單輸入 head 與 CAS 採用

LocalUnionInputs 與 0012 migration 將固定 union_inputs 的採用版本持久化；只操作本地 SQLite，不自動抓名單、不觸發外部工作、不寫試算表。

## 初始化、候選與採用

初始化必須明確確認文件 ID、actor 與 code，檢查既有名單／人工值／archives／epoch 及固定 workbook route；既有 head 不能重新初始化。head 保存 input_id、遞增 revision、active／inactive。

roster candidate 從 82 的固定計畫重新計算，綁定 base input、base revision、observation、target、plan ID、排序政策與 code。只接受目前 head 的無 blocker 計畫；觀測與目標作 immutable objects 保存。讀候選時再次重建計畫核對，不能直接置換目標。

採用須明確確認 candidate ID。單一 BEGIN IMMEDIATE 交易內檢查 request 身分、base input 與 revision、全域 publisher 鎖，更新 head 並保存不可變 receipt；任何 DB 錯誤全部回滾。兩個程序從相同 base 提出不同名單，只有第一個能採用。noop 不提升 revision。

request ID 固定同一 actor／code／候選意圖；精確重播或同一意圖換 request 只取原 receipt，不能把較新 head 倒回。actor 是本地可信呼叫參數，尚未接服務身分驗證。

## 計算與發布資格

已採用 head 的 scope，工作若包含 union_inputs 必須符合目前 input_id 且 active。健檢與排刀草稿在 managed scope 還必須提供此版本，不能省略以繞過檢查。新 request 的過期／缺少版本會在改 desired 前拒絕；舊 request 重播仍返回原 job。

claim、hosted dispatch／claim、complete_compute 與 begin_publication 共用 current 檢查。名單採用後，已排隊舊工作轉 superseded、已執行工作即使完成也不能成為目前結果，已 computed 舊結果不能取得 publisher。這不代表強制停止原程序。

採用與既有 publisher 互斥；publishing／publish_uncertain 都不因時間到期解除。採用不清除最後成功工作簿，也不建立重算工作；呼叫端需另以新固定輸入提交工作。

complete 空名單可採用 inactive，使綁定名單的工作失效；重新加入可採用 active 並要求新 epoch 的資料證據。未綁定 union_inputs 的獨立來源收集／控制工作不在此 fence 範圍。尚未實作全域 inactive 調度政策與自動補抓／重算串接。

## 狀態、備份與保留

唯讀狀態分開 union_input_head 與每項 desired 的 is_union_input_current；原發布 receipt 仍可見，但舊名單發布不再標為 current。typed 狀態表新增目前名單版本、名單狀態、工作符合目前名單，為 16 欄；schema layout revision 因而改變，尚無遠端狀態表採用。

head、candidate、adoption 的 object IDs／JSON signature 由現有 retention root 掃描保留，觀測／舊 base／archive 不會因 head 更新被當孤兒刪除。備份沿用完整 SQLite 與 migration checksum 契約；未改動舊 migration。

## 本地驗證與尚缺

local_tests/test_local_union_inputs.py 驗證固定確認、精確重播、雙連線競爭、重啟、DB rollback、queued／running／computed 舊工作 fence、unknown publisher、不刪最後發布、inactive／reactivate、retention roots 及唯讀狀態。

raid 現有 confirmed head 還依 problem head 判斷 stale，尚需 input compiler 把 union_inputs／healthcheck dependency 固定並整合確認資格。其他獨立缺口為 head 變更後自動提交重算／補抓、實體人工欄遷移與共享狀態 publisher。平台 D1 行為、provider 名單完整性與正式採用均未驗證。
