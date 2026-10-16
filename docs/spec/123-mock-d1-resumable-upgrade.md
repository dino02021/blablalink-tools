# 123：Mock D1 可續跑升級與 marker commit

## 實作範圍

upgrade_mock_d1_workspace 僅接收 spec 122 的可信 MaintenanceIntent。全流程持有同一獨占 SDK lock，先讀 D1 authority／bounded history，再核對完整 schema／checksum／epoch；在任何修改前另檢查 spec 121 的 business holds。錯誤 phase、缺版本、改過歷史、未知 schema 或 restore fence 不被自動修復。

authority 必須是完整單列，revision／epoch／singleton 與 nullable fields 型別正確；history 連續、checksum 與前後 schema 對應，初始 epoch=1、base epoch 與既有 prefix 對齊，本輪新增版本只能使用同一 new epoch。不能只見最後資料表就推定完成。

## 執行與恢復

ready_before → 固定 journal → begin → 依序剩餘 revisions → finish → 最終 authority／ready proof → 原子 marker 替換 → 核對新 marker 與相符 journal → 移除該 fence。

任何 SQL 回覆不明或檔案失敗立即停止，不在本輪盲重播。下次由 authoritative D1 的完整進度續跑，已提交 revisions 不再執行 DDL；finish 已提交就只核對完成狀態並處理 marker。marker 前後中斷都保留同一 plan，不回退 schema／epoch。

若本地 journal 遺失，只有完整 D1 plan／history／schema／epoch 與 quiescence 都證明本輪 migrating，才可在獨占區段重新建立相同 journal 後續跑。已有不相符 journal 永遠不覆寫，restore fence 永遠拒絕；本地檔案不是 authoritative database。

marker 固定完整 business＋ledger identity，以 owned temporary file、fsync 與原子 replace 更新。D1 尚未 ready 卻已出現 target marker 時拒絕，不推測是哪一側正確。僅當最後兩側 proof 相符才移除 journal；一般 SDK 在 journal 存在期間不會啟動。

## 完成重播

目前仍是相同已完成 release、marker 已新且無 journal 時，只核對 schema／ready／完整歷史並回同一 receipt，不提高 epoch、不執行 DDL／history writes，也不因升級後的新 running 工作而重新套用「開始升級」的 idle 判斷。過時 release 不可藉此降版。

## 控制與驗收

每次只允許固定 frame，SDK native serialization 的實際 bytes 也須在 64 KiB 內，不能只檢查少空白的 canonical digest。成功 envelope 仍需逐 statement success 與守衛精確整數 1，bool／錯 row 不當 proof。

首輪 24 項 pure／實際 SDK 案例通過（112.96 秒），包含正常資料保留、新 session 重開、舊 session 拒絕、零 D1 writes 重播、五個 commit／marker 中斷點、競爭 migrators、active hosted／scope barrier 拒絕與實際 restore clone 不啟動 SDK。另補 journal 遺失、相同 target 但歷史 chain 不同、guard 型別與 completed replay 遇到新工作等情境，待本輪 focused 結果。

追加後，本模組與 maintenance／policy 的 52 項通過（153.01 秒）；再補歷史 epoch gap 與實際 SDK deadline 的兩項通過（13.04 秒）。Deadline 案例確認 owned child 已停止、journal 仍在，再以 D1 固定進度續跑至相同 epoch；歷史 epoch 不可無記錄跳號。完整 suite 尚待本批回歸，不把分段 focused 數字當正式驗收。

這仍是本地 mock additive upgrades，沒有正式 Worker／D1 account／provider 接線。完整 Root backend、分層大型 schema、ALTER／資料轉換／降版、release distribution 與正式切換仍未完成。沒有 restore 解封入口。

本批完整回歸：1,252 項通過（774.22 秒），22 項本地證據通過，is_production_accepted=false。完整 Root／正式 provider／大型分層 schema 仍未驗收。
