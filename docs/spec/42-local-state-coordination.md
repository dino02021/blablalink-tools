# 本地持久任務與協調

本文件描述 local/mock-sheets-migration 分支目前的離線能力。入口為 application/tasks.py 與 adapters/local_state.py；既有正式流程尚未匯入這些模組。

## 狀態與資料

- LocalStateStore 必須接收明確 SQLite 路徑；測試只使用暫存目錄與 mock 時鐘。
- 每次狀態變更以 BEGIN IMMEDIATE 交易執行；schema migration 保存 checksum，內容不符即拒絕開啟。
- 快照、結果與 checkpoint 以 canonical JSON 和 SHA-256 保存，讀取時驗證內容。
- TaskSpec 固定 command、scope、family、code_sha、輸入版本、依賴與 intent。priority、重試上限及 executor 不影響業務指紋。
- 相同 request_id 必須使用完全相同請求；不同 request_id 可命中同一業務任務。收集與人工發送命令要求 intent_id。
- 排程用 schedule_id 與 UTC 時槽去重，同一時槽不能替換輸入。

## 設定與任務

- create_candidate 驗證健檢設定與基準 head；publish_candidate 將 CAS、版本、head、重算 fanout 一起提交。
- 發布只建立重算任務，不發送 Discord；重播候選發布必須保持相同目標。
- 任務依實際成功的依賴解鎖；失敗依賴會向下傳播，不影響其他獨立聯盟。
- claim 檢查程式版本、executor、優先度、次數、期限及輸入是否仍為目標版本。
- heartbeat、checkpoint、完成與重試均要求有效 generation；過期執行者不能覆寫新結果。
- waiting_for_data 必須以新快照建立工作；resume 僅處理 auth／capacity 等可恢復等待。
- 已知舊結果可在回滾後重用，但必須重新取得發布權限。

## 派送與免費預算

- dispatch 只產生持久票證，不呼叫外部服務。hosted claim 要求票證、run ID 與一致的 code_sha。
- 預算先於派送保留；Vercel CPU 和記憶體需求一起檢查。Actions 使用分鐘需求。
- 派送與執行完成是不同狀態；票證重播受 run ID、worker 及保存的 generation 限制。
- 未知消耗不自動退款；settle_budget 依可信用量結算，超出預估仍保留真實超額紀錄。
- reroute 僅處理未執行且未發布的工作，保留程式版本與不可變輸入。

## 發布權限

- begin_publication 同時鎖定全域 publisher 與所有實際工作簿資源；只允許目前目標的 computed 結果。
- complete_publication 要求所有資源讀回相同 commit_id；完成後才釋放鎖。
- 未明結果保留鎖與資源進度，重啟或時間經過不會自動解除。
- recover_publication 要求可信呼叫者確認舊 writer 停止，再對照全部資源；全部已套用則直接完成，部分完成則沿用 commit_id 並換 generation 繼續。
- 未解決發布時阻擋設定 head／desired target 切換；資料庫鎖與進度 manifest 損壞時拒絕發布。

## 驗證與限制

- local_tests/test_local_state.py 使用 mock 資料與暫存 SQLite，包含兩個獨立連線的領取、CAS、預算及 publisher 競爭。
- 全套命令：python -m pytest local_tests -q -p no:cacheprovider，目前共 124 項通過。
- 本地方法的停止證明、讀回與用量參數是可信介面前置條件；尚無 provider adapter 驗證這些證據。
- 沒有正式工作簿操作、網路入口、雲端派送、D1 adapter 或遠端憑證載入。SQLite 測試不能證明 D1 的交易與平台行為。
- mock Sheets 格子發布與故障注入仍待後續實作。
