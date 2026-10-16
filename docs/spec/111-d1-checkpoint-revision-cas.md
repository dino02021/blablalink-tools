# 111：D1 checkpoint revision CAS

## 資料與行為

Checkpoint 模組獨立宣告 compute_checkpoints 與 compute_checkpoint_receipts，與既有 job／object／head schema 組合在同一 D1。不在缺少 capability 的 session 自動加表或改 marker。

CheckpointIntent 固定 request／ComputeLease／已 sealed object_id／base_revision。每次 fresh save 檢查 active owner、code、generation、expiry、同 DB sealed object 與目前 checkpoint revision，原子寫入新 head、receipt 並驗證寫後條件。

| 輸入 | 結果 |
| --- | --- |
| 第一版 base=0 | revision=1 |
| base 等於當前 revision | 新 head revision+1 |
| fresh request 的 base 落後 | 整批拒絕 |
| 舊 request 在 head 更新後重播 | 回舊 receipt，不倒退 head |
| 舊 generation 使用新 request | 拒絕寫入 |
| 新 generation 使用當前 base | 可接續既有 checkpoint |

讀取回傳 CheckpointVersion，將 revision 與 immutable object_id 一起擷取，再讀相同 object。payload 每次回傳副本，不允許把新版 revision 配上先前資料。Checkpoint receipt 不延長租約，也不是下一步執行的授權。

## 邊界

Generic checkpoint 是最大 32 KiB 的 canonical JSON object。先由 sealed manifest metadata 檢查大小，再讀 bytes 驗證；大型資料使用獨立 snapshots／結果，checkpoint 保存 cursor／引用。

既有 training_collection／training_collection_pointer 需要專用 progress、source state 與觀察驗證，generic API 先拒絕，待完整 collector parity。這不是把通用 checkpoint 當作專用 progress 已完成。

## 證據

七項 SQLite 契約已通過，含 revision CAS／舊 receipt／generation／expiry／unsealed／UPDATE 被忽略的拒絕與 payload 副本。實際本地 D1 已驗證跨重啟、舊 request 不倒退、generation handoff 後續存；合計八項 focused tests 通過。

Handoff 的首個 SDK 案例使用明列 metadata fixture 改 owner／generation；不是 recovery API 驗收。Recovery、免費 admission、跨 SDK instances、domain source compiler 及完整 Root parity 仍待繼續。

Focused SDK 結果已通過，連同純 SQLite 共八項。實際 recovery API 接續 checkpoint 的後續證據見 [112](112-bounded-d1-compute-recovery.md)，已不僅依賴 handoff fixture；本批完整 suite 待回歸。

最新完整回歸：1,142 項通過，22 項本地證據通過，is_production_accepted=false；包含本模組、完整 D1 組合與 SDK 端口回歸。先前完整 suite 的 SDK 失敗原因與修正見 [114](114-fetch-compatible-local-d1-ports.md)。
