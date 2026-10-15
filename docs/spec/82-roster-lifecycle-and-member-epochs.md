# 名單生命週期與加入版本

目前實作為本地純資料轉換及 mock 計算防護。入口為 application/roster_lifecycle.py；不讀取正式名單、不寫試算表，也尚未建立持久 union-input head 採用流程。

## 固定觀測與確認

觀測固定 scope、reference、時間、完整性、來源 revision 的 hash 與最多 300 個 composite MemberKey。顯示名稱只作顯示，允許同名、空白與公式外觀文字，不作身分比對。重複 ID 拒絕。

prepare_roster_change 固定原 union_inputs、觀測、code 及排序政策，產生具內容 hash 的計畫。confirm_roster_change 必須重建同一計畫並比對確認 ID；修改原人工資料、觀測、code 或目標不能沿用原確認。這是純函式依賴檢查，尚非資料庫 head CAS。

只有 complete 觀測能移除成員。partial、failed、auth_required 或 reference 不符只產生 blocker，不生成可採用輸入。完整空名單的目標為 inactive；目前尚未接上 inactive 的工作取消與發布流程。預設保留既有人員順序、新人追加；observed 排序必須明確指定。只有觀測順序改變時預設維持原文件 ID。

## 人工資料與離開封存

人工值以 composite ID 映射，保留 0、空字串與 null；既有成員 row 的其他欄位保留。離開時，原 row、人工值、全部 overrides（含 disabled／clear）及加入版本一併封存，綁定原輸入與觀測 ID。活躍人工資料與 override 依 ID 篩選，不以名稱或列位置搬移。

孤兒人工值、重複 override target、矛盾 roster_state、壞 epoch、壞 archive hash 均拒絕。最多 1,000 筆離開 archive，原輸入與新目標各最多 8 MiB；超出目標容量回 blocker，不裁切歷史。重新加入不自動恢復舊人工修正。尚未涵蓋舊實體格線人工欄的身分盤點與搬移。

## 加入版本與資料沿用

新增／重新加入的 member_epoch 由原輸入、觀測及身分導出 SHA-256；既有成員保留原 epoch。raw、training、collection inputs 與 observation lineage 傳遞同一 epoch map，相容 reference 投影亦保留。map 只接受名單內的 canonical composite key 與 64 位小寫 hash。

健檢對目前所需 epoch 逐一核對，不符或未證明時停在 waiting_for_data，原因 training_membership_epoch_unproven。舊快照即使角色數值完整或人工 override 可填值，也不能證明此次加入的資料。補抓 planner 綁定 member_epoch_hash，產生 membership_epoch_unproven 的 ownership 補抓需求；目前尚未自動執行補抓。

移除成員不要求替存留成員重抓：同一 training 可依縮減名單重算最大值、分數與排名。未使用 epoch 的舊輸入維持原格式；這不代表其歷史加入身分已得到證明。

## 本地證據與限制

local_tests/test_roster_lifecycle.py 與 test_member_epochs.py 覆蓋加入、離開、改名、排序、同名、人工欄封存、重新加入隔離、壞資料與容量。端到端案例實際證明舊養成等待、帶新 epoch 的 mock collector 完成後可計算發布，以及名單縮減可直接重算。

仍需獨立完成持久 head CAS／採用、inactive 工作處理、raid dependency stale 防護與實體人工欄遷移；真實 provider 的名單完整性及 epoch 採集契約不在本地證據內。

持久 head／base revision CAS 與 inactive 綁定工作 fence 已由 83 補上；本節純計畫函式仍不直接寫 head。全域 inactive 調度、自動補抓／重算與舊人工格線遷移尚未完成。
