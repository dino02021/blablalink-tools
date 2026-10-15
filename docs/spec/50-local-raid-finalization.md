# 本地排刀穩定化與最終補刀

domain/raid_finalize.py 接收 RaidProblem 與相符 RoundSelection，輸出固定 FinalRaidPlan。這是離線計算，尚無正式發布或操作確認入口。

## 流程與保留條件

- stabilize_plan 反覆試算自動刀改派；有限缺口改善才接受，循環最多 50 次並偵測重複狀態。
- 無前段缺口時，raw_damage 模式另嘗試降低第三階段實際浪費；不移動 fixed 或 locked 刀。
- preserve_fixed_priority 由高同步、原 slot 處理未落地指定；同王自動刀讓位，優先低於指定同步者，再比同步與有效扣血。
- 讓位只可改派自動刀；指定目標不變、原已落地指定仍落地、總落地數不減、缺口／剩血與所選溢傷指標不增加才接受。
- balance_element_coverage 以雙元素成員的自動刀和無元素成員交換，依順位成本、同步與王序挑選；每次重新模擬並減少缺少覆蓋者。
- recover_unlanded 只重新安排未鎖定 stage=0 自動刀；必須增加落地數。前段清空後只可補 IV-5，其他情況優先合法缺口。
- 以尚未使用的固定來源 ID 補回最多三輪，再重作穩定化、元素交換、未落地恢復與指定保留。
- source_locked_stage 永久保留 IV 來源鎖。重複五王的合法性以是否超過一個有限刀判斷，不依 tuple 順序誤拒絕「IV 鎖定刀先列、有限刀後列」。
- FinalRaidPlan 綁定 problem_id、source_selection_id、algorithm_version、剩餘來源、warning 與變更摘要；draft_plan_id 為完整內容 hash。

## 第三階段溢傷的來源差異

舊 GS _getRaidRoundPhase3OverflowBosses 累加已被 min(damage,remaining) 截斷的 effectiveTrue，再減去第三階段容量；在一般合法模擬中，這個 gate 為 0，即使真實刀傷害大於剩血。

- RaidSimulation.phase_three_overflow_true 保留真實浪費量，便於診斷。
- RaidProblem.overflow_policy=legacy_effective 為初期相容預設，選刀 gate 沿用舊 GS 指標，不能把它描述成沒有實際溢傷。
- raw_damage 明確改用實際浪費作限制／改善依據；必須由版本化設定選用，並改變 problem_id。不能在移轉時默默開啟。
- 這修正了第 49 號規格早期把 GS gate 描述成嚴格原始溢傷限制的誤判；來源證據與兩種行為都留有測試。

## 驗證與剩餘範圍

- test_raid_finalize.py 涵蓋指定讓位形成雙 IV-5、鎖定不改派、元素交換、缺口改善、政策差異與輸入版本。
- gs_raid_final_reference.js 凍結本地五份排刀來源及 normalizeLabel；provenance 記錄來源 head／hash。
- test_gs_raid_final.py 對照 60 組合成完整最終分配，包含固定、反向、歷史、重複正五與全清尾段，並證明 GS gate=0 可與實際浪費並存。
- Node VM 不提供 SpreadsheetApp、require、fetch；只有固定 runner 載入 fixture，子程序移除秘密與 NODE_OPTIONS。未呼叫來源中的工作簿寫入函式。
- 對照範圍限這些案例的 member／boss／stage／kind／damage；不是所有最適性、同分 locale、顯示格式或最壞執行時間的證明。
- Python 交換／恢復另外保護原已落地指定與總落地數，此保護比部分 GS helper 嚴格；其他資料情境可能有不同結果，需擴充比較，不宣稱無差異。
- 尚需草稿確認、成員 ID 輸出排序、原始指定保留的工作簿呈現、deadline／checkpoint 與完整 A15／A16 驗收。
