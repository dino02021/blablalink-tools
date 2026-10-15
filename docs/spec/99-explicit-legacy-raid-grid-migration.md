# 99：舊人工排刀格線的明確身分遷移

## 支援的已知 mock 範圍

`adapters/legacy_raid_grid.py` 只支援已核對本地 GS 的一種資料排列：成員 A2:B 為名稱／sync，排刀 A3:W 的 A 欄為顯示名稱、P:W 為三個指定刀與五個覆寫。其餘排刀 B:O 仍保存於原始來源，不推論為可刪除區。

mock inputs 必須明確提供 legacy_raid_member_rows（二欄）、legacy_raid_plan_rows（二十三欄）、typed legacy_raid_history、legacy_raid_is_complete=true 與空 legacy_raid_formula_cells。每個範圍最多 1000 物理列，非 scalar／超過 1024 字的 cell 拒絕，完整來源最多 8 MiB。沒有 metadata 不猜測。

此入口是本地資料結構遷移：讀取固定 mock ranges 後，在同一 inputs 新增 98 的 raid_member_grid。不是已對真實 Google 工作簿插欄／套 protection／搬 tab，也不讀正式來源。

## 明確列身分與計畫

prepare_legacy_raid_grid 必須取得兩份明確的 physical-row→canonical MemberKey.key 對照：成員範圍實際 row 2 起，排刀範圍實際 row 3 起。兩張表可以不同排序，同名／改名不參與 join。不能提供空 mapping 期待自動猜名；未知／重複 ID、漏員、空白列上的多餘 mapping、帶資料卻沒有名稱／對照的列均拒絕。

兩份有效 mapping 都須完整一對一覆蓋固定 union_inputs 的目前名單；本批不自動捨棄離隊者或未知列。空白尾列只在全部儲存格為 null／空字串時可略過。歷史仍需符合 98 的 typed identity／kind／count 契約。

LegacyRaidGridPlan 固定 resource、layout revision、union inputs ID、captured_at、code、完整來源 hash、兩份 mapping、供核對的舊 row／label／新 ID links、完整 target JSON 與 raw grid snapshot ID。確認時從目前來源重新建立相同 plan，不能只相信 caller 提供的 target；偽造值或不同 code／確認 ID 都拒絕。

plan 綁定的是明確傳入的 immutable union snapshot，不自行把它宣稱為 coordinator 最新 head。後續 source candidate／compiler 仍須通過目前已發布健檢與名單 gate。

## 套用與保存

MockSheets.apply_legacy_raid_grid 使用同一 workbook thread／OS lock 連續包住讀取、重驗、修改及 atomic save。預覽後來源格或 layout 變動即拒絕；不相關人工備註可以更新，套用會保留取得鎖時的最新值。

僅新增 raid_member_grid，保留所有舊輸入、manual tabs、generated、commit 與 layout revision。指定刀與覆寫不清洗成預設值，0／空字串／null 按 98 保留；按明確 ID 對應後再以 roster 順序形成新格線。不自動發布設定、採用 raid source、排工作或刪除舊區。

每次新套用保存完整原始來源與 plan 到 raid_input_migrations，驗證全部 receipt 的 plan／source hash 與唯一性。最多二十份，超限阻擋，不偷偷丟歷史。整本變更後 mock JSON 限 16 MiB，超限在 save 前拒絕。原本已有 raid_member_grid 時不允許新遷移覆寫。

精確 replay 必須符合原 receipt、完整舊來源 hash、新格線 JSON 與 layout，才只回原 grid snapshot ID；不重複新增 receipt。若新區已有人修改，不用重播覆蓋回舊值。資料與 receipt 同一次檔案替換保存。

## 證據與缺口

`local_tests/test_legacy_raid_grid.py` 二十四項案例包含同名與獨立排序、逐格保留、預覽／確認／重播、七種來源／意圖變動、九種不完整身分／格式、既有新區／新編輯、偽造 target、兩種實際 thread 互斥、archive integrity 與容量拒絕。

正式列 mapping 的正確性仍需人類確認或獨立可信身分資料；本批只使用 mock 已知對照。其他舊模板、離隊未知列的保留／封存策略、正式 formula／protection／range fetch、實體新輸入區建立與 archive／receipt 清理仍未完成。不得因本地遷移案例通過就將正式或全部舊人工資料驗收標成完成。
