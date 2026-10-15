# 本地原始資料與養成正規化

domain/normalization.py 只處理顯式傳入資料，不呼叫 API、舊設定或登入程式。來源欄位依本地 lib/api.py、lib/jobs/nikke_batch_download.py 與 lib/gui/dialogs/nikke_upload.py 核對，測試資料全部合成。

## 原始快照

- capture_raw_training 的 source_schema_version=1，按 area_id／member_id 保存兩種來源：ownership 與 details。
- 來源須明確標示 complete／partial／failed／auth_required，不能只傳空陣列推定結果。
- ownership 白名單為 name_code／grade／core；details 保存技能、四部位裝備、收藏品、數字後綴 equip_option 欄位與 state_effect 的 id／function_details。
- function_details 只保留 function_id／function_value。未列入欄位、header、authorization 等不進入快照；scalar 欄不接受任意巢狀物件。
- details 另外保存 requested_codes 與 is_options_complete；這些目前由可信本地 collector 呼叫者提供，尚無遠端來源證明。

## 身份與完整性

- normalize_training 必須提供同一 reference_version 的完整角色 ID 集與五位數字詞條前綴→function ID 字典；不是只提供當下追蹤清單。
- 拒絕重複成員、角色、state effect、未知角色、超出請求範圍的 detail 與來源互相矛盾。
- 成功完整 ownership 名單中不存在才是 not_owned；部分、授權失效及失敗來源的缺項是 unknown。
- 確認 owned 但無 detail 時保留持有身份，列入 missing_details；不把擷取失敗當未抽取。
- NormalizedCollection 提供安全 issue code 與 is_auth_required。training 內容綁定 raw_snapshot_id 和 normalizer_version=1。

## 數值規則

- breakthrough=grade+core；任一未知則未知，不沿用舊流程的無效值轉 0。
- 技能、等級接受非負整數；拒絕 bool、非有限值、全形、底線數字及不精確大整數。
- 裝備 tier=0 對應 -1，tier=9 對應 T9；其餘已知 tier 用 level，未知 tier 不推定。
- 收藏品沿用舊轉換：tid 首位 2→SR15，其餘依尾位與等級產生 R／SR；明確 N 對應數值 0，缺資料為未知。
- 所有取得的 equip_option 依五位前綴對應穩定 function ID。沿用舊解析的首個 function_details，function_value 除 100 後取絕對值逐項累加，Python round(...,2)。
- 未追蹤詞條也保留；更換追蹤項目可直接投影現有快照。缺少新 function ID 時由完整性閘門拒絕計算。
- 只有 complete details 且 is_options_complete=True 才能把不存在的已知詞條視為 0。缺 effect／數值錯誤及部分覆蓋保持 None，避免部分總和冒充完整總和。

## 證據與剩餘工作

local_tests/test_normalization.py 涵蓋上述規則，並串接 calculate_healthcheck 驗證換追蹤詞條不需重抓、未知詞條阻擋。

這不是線上 collector，沒有回應碼／分頁／session 偵測、重試、完整名單來源證明、月份歷史刀與參照資料發布。未知詞條前綴列 issue，後續 collector／reference 流程仍須處理。首個 function_details 與五位前綴是現有來源相容契約，並非已驗證所有未來 API 變種。
