# 94：共享狀態表本地發布

## 範圍與來源

本批只操作帶 mock marker 的本地 SQLite 與 JSON 工作簿，不連 Google Sheets、GS、provider 或 hosted 平台。`LocalSystemStatusPublications` 使用既有唯讀 `read_system_status`，路由由 `capture_system_status_route` 建立，再以內容 ID、base revision、actor 與 request 明確採用。0017 保存路由、觀測內容與觀測次序；與其他 coordinator migration 一起檢核 checksum。

路由固定 code、resource、layout、排序後的 1–100 個不重複 scope 及 enabled。專用 owner 為 `system_status`，不得包含在觀察名單，也不得與 union membership head 共用。這是本地可信管理者契約，尚無遠端 principal 授權。精確 request／receipt 重播不把新 head 倒退；新採用必須 CAS 且全域 publisher 空閒。不同 code 或 disabled 不排工作。

## 內容與次序分開

唯讀 snapshot 保留目前 desired、每 family 最近成功、blocked／pending、raid 與 workflow 等資訊；單一 scope 的壞資料保留錯誤列，不隱藏其他 scope。global 為既有維護者視角的共用鎖、預算與訊息統計，不代表租戶隔離授權。

semantic observation 綁 route、code 與完整 status hash。單純檢查時間改變只更新 last_checked_at，不建立新工作。A→B→A 重新使用第一個 A 的不可變內容，但建立 revision 遞增的 occurrence，避免舊成功 receipt 被當成這次發布完成。occurrence 的 captured_at 才是此次快照觀測時間。路由或 occurrence head 改變即使舊 computed 工作失效；已取得 publisher 的版本改變仍由既有全域鎖阻擋。

觀測在同一 coordinator transaction 內讀取另一條 query-only connection 的一致 snapshot，先確認無 publisher；計算預算超限時整個 transaction 回滾。8 MiB observation、100 scopes、每 scope 既有 family 上限和輸出 16 MiB staging 上限皆維持。這些是容量拒絕，不截斷成功資料。

## 工作與輸出

`publish_system_status` 為 AUTO command，family／scope 固定 system_status，綁 route 與 occurrence。計算只讀封存 observation，驗證全部 ID／hash／code，產生專屬 typed workbook；不把狀態版本冒充 training version。

固定三張 protected generated tab：聯盟狀態 1、聯盟狀態 2、狀態快照資訊。前兩張各最多 50 scopes，沿用十八欄狀態 view；第三張列出觀測時間、scope 數、狀態版本、code 與「成功紀錄不代表遠端目前工作簿已回讀」說明。scope 數或資料列數改變不新增 tab，空頁仍有表頭。manual tabs 與 inputs 依既有 publisher 契約保留。

worker 使用單一 `LocalWorkerStore` connection，同時具備健檢 workflow 與共享狀態能力。每次 bounded invocation 結尾至多觀測一次已採用且 code 相符的路由，排出的狀態工作由後續 invocation 處理；不開背景 loop。未採用路由則不產生工作。compute-only 與 native child 都不寫生成區；只有明確 `is_publish_enabled` 才走既有 staging／全域 publisher／commit readback。未知 publisher 只回 publisher_busy 並保留鎖。

已排程 snapshot 可以在來源之後變動時仍按其封存時間發布；本批沒有宣稱每次寫入都反映瞬時最新來源。結尾觀測會為下一輪排出新版本。它也無法在全域發布狀態不明時自行更新共用表，唯讀本地 status 仍可檢查。

## 證據與未完成

`local_tests/test_system_status_publication.py` 覆蓋 CAS／replay、非法 scope、時鐘去重、A→B→A、compute-only／實際三 tab mock 發布／無自我回饋、結尾排程、100 scopes 雙頁、route stale／disabled／code、計算與觀測預算、binding、未知鎖、壞 scope、native child、retention 與 fenced backup restore。

仍未完成：真實 Sheets adapter／遠端路由與 ACL、完整歷史分頁／清理、跨平台鎖與 hosted executor 驗收。雙頁是目前狀態容量安排，不是完整歷史查詢。不得把本批列為正式驗收或解除 restore fence。
