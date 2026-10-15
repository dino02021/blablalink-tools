# Mock 工作簿的實體格線與保護

adapters/mock_grid.py 將封存的 WorkbookView 確定性轉成 generated_grid；MockSheets.apply 與 generated payload、commit marker 於同一次本地檔案替換中保存。它不呼叫 Google API，不代表遠端 Sheets 的交易或 protection 已驗收。

## 來源與呈現

- view_id、config_id、snapshot_id、calculation_id、code_sha 必須和重建後的 view 完全一致。
- 每個 tab 保存 typed cells、note、foreground/background、NUMBER/TEXT 語意、尺寸、穩定 column/row ID、合併、凍結、隱藏欄與 generated protection。
- 替換整個產生區，縮短成員列時不留下舊尾列；數值不轉成百分比，公式樣式字串仍是 stringValue。
- 合併的非錨點不可含資料或 note，避免不可見資訊被吞掉。

## 人工區與結構邊界

set_manual_tab／edit_cell 是 mock 協作者編輯入口，僅可修改人工 tab；產生 tab 的 edit_cell 與同 key／同 title（忽略大小寫）的人工 tab 建立會被拒絕。人工 inputs 與 manual_tabs 於回寫時保留。低階檔案修改仍可用於故障測試，不是防惡意本地使用者的安全邊界。

初次建立的產生 tab 不得覆蓋同 key／title 的人工 tab。已有產生格線後，tab 集合或 layout_revision 不同，或試圖改成無 view 的 payload，一律要求明確 migration；目前這個入口尚未提供 migration 確認，不能藉一般回寫自動採用新結構。值、列數與名稱等非結構變更可正常替換。

## 回讀與故障

observe 與相同 commit 重播均重建預期格線並比對完整內容。僅 marker／payload 相同，但格子、顏色、merge、protection、凍結或隱藏欄遭修改，不算成功。既有 publication unknown／可信 reconciliation 規則繼續適用。

local_tests/test_mock_grid.py 覆蓋 literal／格式／尺寸、縮短清尾、人工保留與保護、六類格線破壞、人工 tab 衝突、結構拒絕、來源與合併 note、完整 demo 八 tab。

後續仍需：有界 migration preview／確認與 archive、十一欄遷移、舊 GS header 相容、真正 Sheets updateCells／尺寸／保護 adapter；全部先用 mock 驗證。
