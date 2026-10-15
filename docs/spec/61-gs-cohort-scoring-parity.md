# GS 全員評分迴圈對照

local_tests/test_gs_scoring.py 以 50 個固定種子的合成 cohort，比較 Python score_union 與凍結本地 GS 的實際評分寫入、加權總分與排名函式。

- fixtures/gs_scoring_reference.js 保留 _collectScoreWritesForSheetRows、_appendHelperTotalWrites、_collectTotalRankWrites 與必要 helper 原文；gs_scoring_provenance.json 記錄來源 repository head、原檔 hash、函式範圍及 fixture hash。測試不再讀取鄰近專案，也不連線 GS。
- Node runner 使用固定 VM 與合成 sheet／range／header config 邊界，無 SpreadsheetApp、require 或 fetch 提供給 VM，關閉動態 code generation，計算有 timeout；子程序清除 provider 秘密及 NODE_OPTIONS。
- 逐一精確比較每成員／角色分數、五屬性與輔助加權總分、competition rank。案例涵蓋 1..9 成員、1..8 角色、多屬性、輸出／輔助／雙類型、忽略追蹤、零權重、正負係數、浮點權重、R／SR／T9、空白、零 cohort 與同分。
- mock rows 刻意插入不在有效 roster 的高數值空洞，驗證最大值只使用有效成員。GS 的角色／總分計算迴圈不改寫成 Python 算法。

這是評分迴圈到寫入值的契約對照，並非完整 GS 工作簿測試：header→term 映射、nickname lookup、merge、顏色與真實 cache 都由合成邊界提供。雙類型 metadata 由已定義的 Python v2 設定映射，不能藉此宣稱舊 GS 首項查找已支援所有 v2 佈局。尚需完整 workbook renderer／layout fixture 與正式切換前的離線差異分類。
