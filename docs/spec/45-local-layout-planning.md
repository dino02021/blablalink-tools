# 本地欄位計畫與儲存格請求

domain/layout.py 提供純函式模型；MockSheets 的 layout 方法僅用於離線預覽／確認測試，尚未成為正式發布入口。

## 欄位身份與預覽

- Column 以 column_id 識別，label 可以改暱稱；owner 區分 system／human，格式支援 text／number／percent。
- Layout 固定實際資源、數值 tab ID、欄位順序及 schema_version=1，以 canonical JSON hash 作 revision。
- prepare_layout 對既有 system 欄更新、封存或恢復；新欄附加於尾端，不移動人工欄。desired 順序不會重排既有實體欄。
- 移除預設只封存，不真正刪除；角色或 function ID 恢復時沿用原位置。不得將人工 column_id 宣告為系統欄。
- LayoutPlan 包含 before／after、固定設定與程式版本及操作摘要；plan_id 綁定完整內容。
- confirm_layout 核對明確 plan_id、當前 base、設定與程式版本，並重建計畫驗證，拒絕偽造 after 覆蓋人工欄。
- mock apply_layout 保存原 layout 與生成資料，再套用新 revision；不改 inputs。此方法不代替中央 publisher 權限。

## 渲染

- render_columns 接收唯一成員 ID、完整的 member／column 值映射與結構化 assessment status。
- 只輸出 active system 欄的精確零基底 updateCells 範圍；跳過人工及封存欄。
- 每欄第一列為標題；後續按明確 member_ids 順序輸出。此幾何是新模型，尚未映射舊 GS 多列標題模板。
- 明確使用 stringValue、numberValue、boolValue 或空值，文字即使以等號開頭也不生成 formulaValue。
- 身份 ID 應為字串；拒絕非有限數值與超過精確 binary64 範圍的整數。
- status 決定文字顏色與 note，不讀取舊格子顏色判斷達標。previous_member_count 用於明確清除生成區舊尾列。
- 若同表混有人工欄，existing_member_ids 必須與新順序完全一致；禁止排序／成員增刪造成備註錯位。成員名單變動應使用獨立人工輸入區依 ID 對應。

## 驗證範圍

local_tests/test_layout.py 驗證欄位封存／恢復、暱稱、新詞條 ID、人工衝突、固定確認、舊 base、偽造計畫、型別化字串、顏色／尾列及 mock archive。

尚未提供舊 GS 12 欄模板轉換、屬性分組重排、維度插入／隱藏請求、完整格式保護、遠端 API 或格子級 simulator。請求形狀測試不能代替正式 Google API 相容驗收。
