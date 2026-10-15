# 排刀工作簿視圖與固定確認結果

application/raid_workbook.py 從固定 RaidProblem／FinalRaidPlan 產生 RaidWorkbookView，先經 build_raid_view 核對 simulation、source pool、傷害、指定刀與必要 warning。view_id 綁定 problem_id、draft_plan_id、原 RaidView hash、code 與所有 typed cells。

## 五個唯讀 tab

1. 出刀總覽：隱藏 composite member ID，保留既有顯示排序、同步器與新刀數；五 BOSS 各有傷害／階段欄，兩層合併 header、凍結前兩列與身分欄，多刀保留換行。
2. 出刀明細：每刀一列，綁定 member＋knife ID、BOSS、真實傷害、億單位傷害、階段、歷史旗標及可取得的有效傷害。真實傷害使用 decimal 字串，不轉 binary64，沒有證據的有效傷害保持空白。
3. 指定刀輸入鏡像：依原 roster 順序保留三個原始指定 token 與五個傷害覆寫，與排序結果分開；這是 protected 產生鏡像，不是可編輯的真實輸入區。
4. 排刀警示：保留每個 member ID 與警示代碼，使用 unknown 顏色；外部 member warning 拒絕呈現以免錯配。
5. BOSS 狀態：三階段剩餘真實血量、第三階段缺口與原演算法的 phase_three_overflow_true。精確值仍為字串，缺口標紅；未重新解釋既有 overflow policy。

歷史刀依明確來源呈灰。總覽同格混合歷史與新刀時，保留混合註記並指向逐刀明細；不假裝整格單一顏色能表達每一行來源。所有公式樣式暱稱／指定 token 都經 literal stringValue。每 tab 的 generated protection／merge／freeze／hidden metadata 使用共同 ViewTab；沒有遠端 enforcement。

## 計算與確認邊界

run_raid_job 把 workbook_view／workbook_view_id 和原 draft result 一起封存，仍然 is_publication_required=False，不自動確認、不回寫試算表。

LocalRaidPlans.confirmation_workbook 從確認記錄所指的原始 problem／final 重新產生視圖並核對原 RaidView hash，同時回傳 is_stale／is_current_confirmation。新輸入使確認 stale 時，舊 workbook_view 仍完全固定，不偷偷換成新 draft。輸出只是一份本地查詢結果，尚無正式排刀工作簿 route／publisher eligibility 接線。

呈現前限制最多 3000 成員、9000 歷史刀及 9000 warnings，各 ViewTab 與整本格數也有界；逐成員／警示保留 execution budget 檢查。這不是 hosted 性能承諾。

local_tests/test_raid_workbook.py 六項案例加上既有 raid job 驗收，涵蓋五 tab、重名與 literal、排序不改指定刀、精確大額傷害、混合歷史來源、警示／缺口、stale 確認保持原視圖與錯誤依賴拒絕。

84 的 compiled problem 會在名單或來源健檢目前性改變後立即 stale，不必等待 problem head 更新。原 confirmed workbook 維持原內容，沒有實體 publisher 接線。
