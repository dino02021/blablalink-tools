# 發布的停止證據、原始基準與安全取消

0016 新增 publication safety phase、原始基準、abort plan／receipt 與 request 去重。公開 API 為 prepare_publication_abort 與 LocalPublicationSafety.commit_publication_abort；全部只使用本地 mock adapter，不操作正式試算表。

## 兩種證據路徑

- not_started：該 publication 自建立時即有 safety phase，生成區寫入尚未開始。明確確認所有生成區 writer 停止後，以當代 generation／固定 manifest／完整 resource lock inventory 取消；不查詢工作簿，也不宣稱已核對其內容。
- unchanged：生成區寫入已開始或可能開始，必須有在第一個寫入前保存的全部原始基準。確認所有生成區 writer 停止後，逐資源重新觀測，完整生成區／版面皆相同或已明確恢復原基準，才可取消。

這兩種情況都不把「沒有看到 expected commit」當成未寫入證據。partial apply、內容或版面未復原、marker 與實際內容不符、基準缺失、writer 未停止、generation 改變或鎖清單損壞均拒絕。

is_all_writers_stopped 是可信本地 reconciler 必須提供的明確事實，並須維持 quiescence 到決定完成；不是時間到期、沒有 heartbeat 或網路逾時的推論。現有 mock 呼叫為同步觀測；正式 process／provider 停止證據與 principal 尚未接線。

## 原始基準與送出前標記

新 publication 建立時原子寫 safety phase=0。publish_workbooks 在任何 adapter I/O 前驗證 claim；具 capture_publication_state 能力的 adapter，須在第一個生成區寫入前封存全部工作簿基準。每次 apply 前先持久標記 send_started=1，並重驗當代 owner／generation。

MockSheets 基準固定 resource／publication／commit／result／code。生成區證據包含 layout_revision、舊 commit／payload hash、完整 generated hash、typed generated_grid hash 與 layout hash，或明確 missing resource。已有內容須通過 payload／grid 一致性核對；不只相信 commit marker。

人工 inputs／manual tabs 不在生成區比對內，取消本身也不寫工作簿，故可保留失敗後其他人的人工編輯。原始基準不能被 overwrite；recover／重試沿用第一份基準。開始送出後不能補造缺失基準，舊 publication 沒有 safety phase 時也不推定為未開始。舊 port 可沿原 retry 契約運作，但沒有這個取消資格。

## 原子決定與終止帳本

abort plan 固定 publication／generation／job／result／commit／code／resource manifest、actor、證據模式及必要的 base／observation IDs；commit 必須明確確認該 plan ID，再次確認 writer-stop 事實。單一 SQLite 交易重驗 metadata、完整鎖、全部證據，保存 immutable plan／receipt／request、提高 generation、將 job 設 failed／publication_aborted_no_write，再僅釋放這個 publication 的鎖。

publication 原表的 CHECK 與歷史 storage state 不改、不冒充 succeeded。publication_abort_receipts 是終止決定帳本；publication_effective_states view 將有決定者投影為 aborted。publisher gate、公開 safety metadata、狀態 hold 與 backup quiescence 使用 effective state；成功 receipt／last published 仍只算真正 succeeded。

精確 request／plan 重播只回原 receipt，不倒回 head、不修改後來 publisher 的鎖。舊 writer／舊 generation 在 adapter I/O 前被拒絕；已取消工作不能 recover 為 publisher，也不自動重排。需要新固定任務意圖／來源版本，不能藉取消抹掉失敗歷史。

## 恢復與保存

若全部工作簿已套用但回應遺失，使用既有完整 commit 回讀的成功恢復；不能取消為 no-write。若只有部分套用，保留鎖，繼續相同 commit 的有界恢復或另行取得完整還原證據。

原始基準、plan／receipt 與觀測均是 retention roots／傳遞引用；backup 可在有效終止後進行，保留原始歷史，restore 仍有 fence。未知且沒有終止證據者不能靠改 job 狀態就通過備份。

## 本地證據與後續

local_tests/test_publication_safety.py 的 18 項案例涵蓋未開始／全基準、停止事實／時間、人工編輯保留、partial／response loss、舊 phase 不推論、缺基準不補造、generation／確認、偽造模式、DB rollback、鎖損壞、重播與新 owner、marker 不足、基準不可改、重啟／retention／backup／restore，以及實際八 tab 健檢格線完整復原後取消。

本批未實作自動 rollback、遠端 principal 或 Google 停止／回讀證據。另發現 MockSheets 的人工編輯與生成區更新仍使用整份 JSON read／modify／save；跨程序同時編輯需要加入互斥，避免覆蓋彼此已更新的欄位。這項本地模擬器遺漏將另補實作與並行驗證；不能以目前循序人工保留測試推定多人並行已完成。

93 已補上公開 MockSheets read／modify／save 的 thread／process 互斥與實際並行驗證；私有低階直接寫檔不在此契約內，正式 Google partial range／CAS 仍未接線。
