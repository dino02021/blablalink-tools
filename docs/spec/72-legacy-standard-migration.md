# 十一欄標準的明確 mock 草稿遷移

adapters/legacy_migration.py 支援一種已知舊模板：六列、從 B 起每角色十一欄、缺少突破欄。只接受明確 physical column count、每個起點的 protected role ID、merge inventory 與 cell note map（可為空）。不猜測缺失 metadata，不處理任意破損工作簿。

## 預覽與資料保存

prepare_legacy_migration 產生不修改來源的 LegacyMigrationPlan，固定 resource／layout_revision、scope／captured_at／code、完整 pinned catalog hash、所有標準來源欄位的 hash、target config_id 與完整目標 inputs JSON。plan_id 為以上資料與完整 column_map／inserted_columns 的 hash。

- 每個角色在原技能前插入一欄，原技能／裝備／收藏品／三詞條逐欄對應，突破門檻與係數保持空白，不能預設零或替玩家設定標準。
- 元素／角色／總權重的錨點保留在新 block 起點，對應 note 一起移動；技能等 metric group 的錨點隨原欄右移。
- 保護角色 ID 的 1-based 物理起點重映射，並產生 [element, role ID, metric ID] 組合 column identity。來源若已有 identity，必須和已知十一欄語意一致。
- 只接受已知 header／技能／裝備／詞條／權重 merge 形狀。重疊、跨角色的 role／weight merge、非錨點隱藏 value／note 均拒絕。元素 group 可涵蓋完整連續 blocks。
- 來源標準欄位 canonical JSON 最多 8 MiB，六列精確寬度最多 9000；不吞掉額外尾列或未知欄。
- 轉換結果必須先通過十二欄 read_legacy_standard，包括 pinned reference、別名、ID、元素、詞條與標準一致性檢查，才得到預覽 config_id。

## 確認與草稿邊界

MockSheets.apply_legacy_migration 重新由目前來源與 catalog 建立相同計畫，要求 confirmed_plan_id／config_id／code_sha 一致；先完整封存舊標準欄位與 layout_revision，再於單次本地檔案替換更新 inputs 與新 layout_revision。其他人工 inputs、已發布 generated／commit 均保留。最多 20 份 archive，達上限拒絕，不自動清理。

完全相同的已套用計畫可重播而不重複 archive；之後標準草稿或 schema 改動則拒絕重播。此入口不混用另一種 Column Layout 的 ownership；已有該 layout metadata 會拒絕。

遷移只更新標準草稿，不發布 config head、不排 job。reconcile_legacy_standard 可將轉換結果建立為與預覽 config_id 相同的固定候選；仍需既有設定確認／發布流程。來源 GS metadata fetch、格式／保護／寬度等完整遠端移轉未接線。

## 本地驗收

local_tests/test_legacy_migration.py 的 26 項案例驗證逐格保存與空白突破、合併／note 座標、完整 archive／人工保留、十一種依賴變更、九種破壞形狀、拒絕覆蓋後續草稿、只建候選與 archive 上限。全部為合成資料，不讀寫正式試算表。
