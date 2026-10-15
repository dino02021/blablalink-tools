# 同源健檢工作簿視圖

domain/view_grid.py 定義有界 ViewCell／ViewTab／WorkbookView；application/workbook_views.py 從同一 config／training／cohort／display names／overrides 與可信核心計算結果產生完整 generated view。它是獨立 schema，不冒充所有正式 GS 版型。

- 一份 WorkbookView 含標準、五個屬性練度、評分、報告，共八 tab，固定 config／training／calculation／code IDs。run_healthcheck_job 將 view 與 view_id 一併封存並走原 mock publisher／staging。
- 名稱、人工修正與 roster 的 input hash 必須符合 calculation；不能在完成計算後換顯示來源或重新讀目前草稿。覆寫只套用投影，不改原 training。
- 每格為明確 scalar；typed_rows 將字串轉 stringValue，包含等號開頭名稱也不當公式。數字須 finite，超出 binary64 精確整數範圍需使用字串。
- 標準沿用六列／十二欄 block：屬性、角色、項目、門檻、係數、總分權重，保留技能／裝備／詞條 merge，詞條 label 由固定 function ID 對應。共享 METRICS／ABBREVIATIONS，避免 decoder 與 renderer 各自漂移。
- sparse 規則未選 metric 的格子保持空白；可經 legacy decoder 驗證已選門檻／係數／詞條，但 decoder 會將其他 blank slots 顯式化，不宣稱整個 sparse config snapshot hash 可 round-trip 相同。
- 練度每 tab 兩列 header、每角色十二欄，成員使用 composite ID，display name 可重複。訓練缺項直接用 evaluate_metric 的相同語意；failed 紅、unknown 琥珀、not_owned 灰且維持空白，不補成零。
- 評分從同核心結果顯示角色分數、屬性／輔助總分與 competition rank，數值 0..100 用 NUMBER 而非百分比，以免 100 變 10000%。報告保持原完整文字與 warnings。
- 所有 tab 為 generated-protected 所有權宣告；成員 ID 欄隱藏且保留 metadata。這是 view 契約，尚非 Google 保護範圍的遠端 enforcement。
- row／column 身份、合併、凍結與隱藏維度明確保存；layout_revision 不含資料值，資料改變不假裝結構改變。每 tab／整本上限 500000 cells，單維度上限為本地契約，非 Google 平台額度承諾。
- merge 不能越界、重疊或隱藏非空值；模型還原會重新驗證。生成結果保留舊 scores／reports／standards 欄位，view schema 自行為 v1。

local_tests/test_workbook_views.py 驗證八 tab、source 綁定、重名／公式樣式名稱、缺項顏色、not_owned 空白、標準回讀、錯 names／override 拒絕、套用同 override 不改 raw training、資料變更不改 layout revision、合併與 ownership 防呆，以及實際 mock 發布後重新還原 view。

尚未產生真實 Sheets batchUpdate、套用遠端 protection／merge、清除遠端舊尾列或完成所有舊版 header 相容；新 view 的 training／score header 有自己的 schema。mock grid materialization 見 [70](70-mock-physical-grid.md)，明確 layout 遷移見 [71](71-mock-grid-migration.md)；不能直接將 raw view.value 以 USER_ENTERED 寫入正式表。
