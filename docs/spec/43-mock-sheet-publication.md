# Mock 工作簿發布

adapters/mock_sheets.py 僅模擬本地工作簿，不提供任何 Google API、憑證或網路入口。application/publication.py 串接本地狀態庫與此 adapter。

## 寫入與驗證

- 每個合成資源存為獨立 JSON 檔，檔名以資源 hash 決定，不能以資源字串跳出暫存目錄。
- 人工 inputs 與 generated 分開；生成區完整替換會清除舊尾端資料，保留人工輸入。
- 寫入前核對 layout_revision；模擬以暫存檔 replace 同時提交生成內容、hash 與 commit_id。
- 同 commit_id 重試時核對內容，符合才跳過，不允許同一 commit 改內容。
- observe 同時驗證 commit_id、保存的 payload hash 與實際生成內容，不能只信標記。
- publisher 要求 outputs／layout_revisions 等於狀態庫封存結果，並和 claim 資源集合一致；每次寫入前驗證目前權限。
- 已成功工作重播會讀回驗證，無須再寫入。舊 generation 不能寫入或改變新 publisher 狀態。

## 故障與恢復

- before 模擬寫入前失去回應；after 模擬寫入已完成但回應遺失。
- 例外後保留 publish_uncertain 與所有鎖；跨工作簿不宣稱原子成功。
- 可信 reconciler 先確認舊 writer 已停止，再讀回全部內容。全部完成直接結案，部分完成沿用 commit 並跳過已完成工作簿。
- 測試會重建檔案 adapter 與 SQLite store，證明恢復不依賴舊程序記憶體。

## 證據與限制

- local_tests/test_mock_publication.py 涵蓋人工編輯、舊資料清除、layout 衝突、內容破壞、權限、部分成功、回應遺失與重啟。
- 此模型假設單一已授權 writer，沒有模擬 Google 的並行 HTTP、公式、格式、範圍保護或配額。
- 本地檔案替換不是正式 Sheets batchUpdate 的驗收證據；尚未處理分片 staging 與完整工作表 layout planner。
