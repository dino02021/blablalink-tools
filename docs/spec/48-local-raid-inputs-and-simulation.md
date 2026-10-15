# 本地排刀輸入與階段模擬

domain/raid.py 為純 Python 模型，尚未包含完整輪次選刀或任何工作簿讀寫。

## 身份、單位與來源

- RaidMember 以 MemberKey 識別，顯示名稱不當主鍵；同步必須為 1..100000 的整數。
- RaidKnife 保存固定 knife_id、來源、boss 1..5、順序、階段與鎖定來源。history 必須鎖定 1..4 階；IV 只有五王。
- 真實傷害與 HP 使用十進位字串，最多六位小數、上限 10^24。模擬局部 precision=50，不依賴 caller Decimal context。
- 估傷刻意沿用 GS binary64 的 pow(sync,3)/pow(base_sync,3)*base_damage，最後 JS 正數四捨五入；拒絕超過精確整數範圍的估傷。
- 億轉真實傷害採整數乘 100000000，避免 JSON 數值傳輸及 Decimal 外部 context 損失。

## 歷史與指定

- parse_history 支援 CR／LF、多行、單一階段廣播；保留逐刀身份。無效行回傳 warning，不會假稱整份來源完整。
- 歷史階段只接受 I／II／III／IV 或整數 1..4；不沿用 GS 寬鬆的 1.9→I。正式 collector 必須阻擋未處理的歷史 warning，才能發布排刀。
- FixedInputs 固定三個原始 slot 與五個傷害輸入；prepare_fixed 不清除被忽略輸入，另回 warnings，符合遷移保留人工輸入要求。
- 正／負與一～五／1～5 皆可；同王後續指令忽略，只有連續有效正五可重複。
- 負向不占刀、加入 blocked_bosses。歷史撞王、已滿三刀、無效 token 依既有處理順序列 warning。
- 正向有有效傷害覆寫才使用，空白／0 或無效值用估傷；無效值另列 warning，無用傷害輸入仍原樣保留。
- 歷史五王之後的正五及重複正五鎖定 IV。source_locked_stage 獨立保存，即使本輪無法落地變為 stage=0，重模擬仍恢復 IV 鎖定。

## 階段模擬

- 嚴格要求三個有限階段、每階五王；先扣歷史刀，從不移動歷史。
- 計畫刀按每王、鎖定優先、同步低到高、顯示名稱、輪次與順序分配；最後以穩定身份打破同名平手。
- 每王獨立從 I→III 落點，傷害只扣所在階段，溢傷不帶到下一階。
- 五王尾段等全部十五個有限 HP 清空才開放。輸出 effective_true、剩餘 HP、缺口王與第三階段溢傷。
- 新刀不得超出 3 減歷史刀數，也不得重複同王；五王重複必須鎖定 IV。
- 同名排序目前為 Python Unicode 字串順序，尚未證明等同 GS localeCompare；完整選刀移轉前須固定跨平台排序鍵並列差異。

## 證據與剩餘範圍

local_tests/test_raid.py 驗證多行歷史、指定順序、原始輸入保留、同王規則、尾段鎖恢復、大數精度與溢傷。

gs_raid_reference.js 凍結 8 個 GS 純函式；來源 head、各來源及 fixture hash 見 gs_raid_provenance.json。test_gs_raid_reference.py 以 100 組估傷及 40 組有限階段案例對照。本地 Node VM 不提供 require／fetch，子程序移除 provider 秘密及 NODE_OPTIONS。

這些案例不涵蓋完整候選池、前三輪、補洞、改派、指定刀優先輪替、燃燒／鐵甲交換、缺刀草稿確認與效能上限。不得以本層通過宣稱 A15／A16 完整驗收。
