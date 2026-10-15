# 98：穩定 ID 人工排刀格線與封存來源

## 為何新增契約

本地 GS raid_plan_data.gs 的既有 reader 依輸出列名稱與 normalizeLabel 對應成員，再讀三個指定刀與五個覆寫。重名／改名與輸出排序可能造成錯配；新核心不延用這個名稱 join。這裡只建立新式本地 mock 輸入契約，未讀正式工作簿，也不假稱舊表已自動加上穩定 ID。

`capture_raid_member_grid` 封存 raw grid，`derive_raid_member_inputs` 產生既有 compiler 接受的 raid_member_inputs。兩份皆為 immutable union_inputs 文件，scope／reference 相同，raw grid 必須固定該次 union_inputs snapshot ID。display name 只從固定名單取得，不能作為 join key。

## 格線

grid 僅包含 schema_version=1、union_inputs_id、column_ids、rows、history、formula_cells 與 is_complete=true。固定十欄依序為 member_identity、sync、fixed_1／2／3、damage_1_yi 至 damage_5_yi。column IDs 不是可猜測別名；缺欄、額外欄或不完整來源拒絕。

最多 1,000 物理列、300 個目前成員，每列十個 scalar cell；全空白尾列可略過，但有效列必須完整覆蓋整份名單。member_identity 為 canonical JSON `[area_id,member_id]` 字串，必須完全符合 MemberKey.key。未知、重複、缺員、非 canonical 或暱稱皆拒絕。格線排序可改，衍生輸入只按固定 roster 排列。

sync 接受正整數、整數值的 finite double 或 1–6 位 ASCII 整數字串，最後限制 1–100000；boolean、小數或含公式的內容不當正常同步器。三個 slot 僅接受字串或 null，null 明確轉為空字串。原 slot token 不先清洗或覆寫；既有 Python prepare_fixed 負責 token／重複刀／歷史與警示邏輯，公式樣式字串不在本地求值。

五個覆寫保留 0、空字串與 null 差異。finite double 的整數值轉 int，其他轉 decimal 字串，以符合 FixedInputs；數值 double 超過精確輸入範圍 2^53 拒絕並要求 decimal text。raw grid 仍保留原始值。單格文字最多 1024，非 scalar／非 finite 值拒絕。

formula_cells 必須是空 list。這只是 mock capture 所宣告的輸入條件，不是 Google 遠端儲存格未含公式或身分欄已受保護的證據；正式 fetch 還需要實際 formula／protection metadata，literal string 與公式也須由 provider 型別區分。

history 為最多 900 筆明確 typed RaidKnife record，須為相同名單的 locked history、ID 唯一且每人至多三刀；不由暱稱或舊 multiline 格式猜測。此處的完整性聲明不是正式 provider history completeness proof。

## 候選與原始格線 lineage

`LocalRaidSources.create_raid_source_candidate_from_grid` 在同一 transaction 保存 raw grid、衍生 member inputs、compiled problem 與 source candidate。任一來源／route／code／健檢驗證失敗，新增物件與 candidate 一起回滾。

bundle 可選擇新增 member_grid_id；舊 bundle 不變。讀取含該欄的候選時，重新從原 grid 與固定 union inputs 衍生，member_inputs_id 必須完全相符。既有採用／原子 draft followup／worker 接續使用同一 member inputs，raw grid 由 candidate 傳遞引用保留於 retention roots。

預覽或擷取不代表採用；擷取之後的 mock Sheet 編輯不改原 grid／candidate，採用仍明確確認固定 candidate。計算及 draft 不回寫人工輸入區，不自動確認或發布排刀。採用時若原健檢或名單已過期，既有 compiler gate 繼續拒絕。

## 證據與缺口

`local_tests/test_raid_member_grid.py` 二十六項案例與來源／followup 合併五十三項通過，涵蓋重名／重排、raw 值／0／空／null、literal、十六種無效格線、typed history、numeric sync、來源 scope、實際 mock Sheet 擷取後再編輯、候選→採用→worker、失敗 rollback 與 raw lineage retention。

尚未完成舊名稱格線的人工確認遷移、真實 Google ranges／formula／protection fetch、歷史 provider 完整性、入口調度與自動輪詢的 semantic 去重。不同 captured_at 可以產生新 immutable 版本，本批沒有在每次讀取時自動去重／採用。正式與 hosted 環境仍未接線。
