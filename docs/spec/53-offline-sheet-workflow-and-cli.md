# 同源設定與離線整合流程

adapters/sheet_standard.py、application/sheet_healthcheck.py 與 executors/local 串接目前 mock 試算表、狀態庫、快照、計算與 publisher。沒有匯入舊憑證或 Google／Discord／雲端 client。

## 標準來源

- structured mock 工作簿 inputs.standard_rows 保存角色 ID、標籤、屬性、類型、追蹤、權重與 metric 門檻／係數／function ID。
- StandardReference 固定字典版本與可接受角色／function IDs；未知欄位、身份、重複角色、無效 metric 與空表拒絕。
- 空白列只略過，不截斷後續角色。只有 tracking 恰為「追蹤」才納入評分，輸出設定明確保存 is_score_tracked。
- read_standard_sheet 產生不可變設定快照；這是 structured mock 契約，尚未解析正式 GS 橫向標準格。

## Reconcile 與候選

- reconcile_standard 讀目前 mock 草稿，比對已發布 payload／reference；相同則不產生候選，不因新的 captured_at 誤判變更。
- 有變更只建立候選，不發布也不啟動計算；候選固定當時內容，後續協作者修改不納入已確認候選。
- 0004 migration 加入 semantic_id：scope、base、reference、schema 與完整 payload 共同去重，觀測時間不影響業務身份。
- 舊候選缺 semantic_id 時，建立下一候選會核對該 scope／base 的舊物件並沿用原候選，保留第一次捕捉內容與時間。
- candidate_document 回傳實際封存文件；後續任務必須使用該版本，不可用較新的草稿替代。

## 計算與回寫

- run_healthcheck_job 依工作固定的 healthcheck_config／training／union_inputs 物件讀取，不在計算途中重新讀草稿。
- union_inputs 固定名單、顯示名稱、人工修正與 mock 輸出路由／layout revision；scope／reference 不符拒絕。
- 缺必要訓練資料進入 waiting_for_data，既有工作簿結果不清空。
- 生成 manifest 的 standards 直接取同一個計算設定快照；config_id、snapshot_id、code_sha、分數與報告一起綁定。不存在獨立的上傳標準來源。
- 中央本地 publisher 驗證完整 manifest 與工作簿讀回，保留人工 inputs。修改門檻可沿用既有完整 training snapshot，不呼叫 collector。

## CLI 與證據

- `python -m executors.local demo --workspace <new-directory>` 只建立新／空目錄中的合成工作簿與 SQLite，模擬明確確認事件；不接受遠端 URL 或憑證。
- `python -m executors.local status --workspace <initialized-directory>` 要求精確 mock marker 與既存 SQLite，不讀取任意正式資料庫。
- demo 先捕捉門檻 5 候選，再把草稿改成 9，仍以已確認 5 計算／回寫，最後 reconcile 只留下未發布 9 候選。
- 不覆寫非空目錄；CLI stdout 只回安全摘要與版本 IDs。
- local_tests/test_sheet_workflow.py 包含同名不同 ID、固定候選、新門檻重用資料、未補齊資料保留舊結果、候選時間去重、舊 schema 候選及實際 CLI 子程序。
- Python 測試有 socket guard；CLI 子程序以清除 provider 秘密的環境執行固定離線入口。父程序 socket guard 不能被描述成會攔截任意子程序網路。

尚未包含正式橫向 GS reader、通用 worker CLI、route／actor 授權、完整候選變更摘要 UI、細部格子格式與 D1／HTTP／雲端入口。不得把此 demo 視為正式切換已完成。
