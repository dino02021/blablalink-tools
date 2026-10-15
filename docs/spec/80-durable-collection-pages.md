# Collector 的持久分頁證據與觀測封存

run_collection_job 現在使用 CollectionCheckpoint。純 collect_training 仍可不提供 checkpoint，既有 CollectionPort 沒有增加 HTTP／憑證讀取功能。

## 頁面、進度與觀測分離

每頁通過原 member／source／scope／reference／revision／total／unique-ID／cursor-cycle 檢查及 whitelist 後，才保存 immutable raw-kind fragment。fragment_kind=training_source_page，包含來源身分、request scope hash、頁序、白名單 rows、頁面 evidence、terminal／options flags。這不是可直接送給 normalizer 的整份 raw snapshot。

opaque cursor 與 source_revision 只保存 hash；原字串、HTTP headers、session 或私密欄不進 fragment／checkpoint。

0011 migration 分開保存 collection_progress／collection_source_progress 的 mutable 進度列與 collection_observations。進度 metadata／source state 有綁定 job／source key 的 canonical hash；每次更新都經 compute lease generation 檢查。job_checkpoints 在進行中指向一個固定的小 pointer，由 load_checkpoint 驗證並解析目前進度。

不在每一頁保存整份愈來愈大的 immutable manifest，避免頁數平方級的歷史 blob。頁面 fragment 線性保存，mutable source state 更新自己的 page ID 清單；本輪得到 raw／training 後才封存完整 manifest。

## 原子觀測與保留根

save_collection_observation 在一個 SQLite 交易核對目前 progress、job code／inputs、raw→training scope／reference／角色 universe／capture time，並一起保存完整 manifest、更新 metadata、記錄該 generation 的 immutable observation 與切換 job checkpoint。任一步失敗全部回滾。

成功、partial／retry 或 auth 觀測都有 raw／training／完整 checkpoint IDs，不把「未完成」混成成功 result。每 generation 不可替換為另一份觀測；後續 retry 保留較早失敗觀測與當時 fragment 的可追溯證據。這些 SQL rows 與 manifest 引用都是現有 retention 根，不能當成孤兒刪掉；之後的 job／history cleanup 需另定保留政策。

## 重啟與沿用

- partial source 不保存可直接重用的 opaque cursor。重啟會從首頁重走，重新取得當次 cursor 並驗證頁鏈；不能宣稱跳過所有已抓頁。
- 預設不假設 revision 跨 invocation 代表完整不變資料。沒有穩定快照承諾時，即使有完成 cache 也重新抓全部頁。
- 只有可信 adapter 明確 is_stable_snapshot_revision=True，才允許驗 fresh head 後沿用完成來源。MockCollection 此旗標預設 false，特定合成案例明確開啟。
- 沿用前逐一驗 immutable fragments、source context／request scope／頁序／hash chain／revision／total／unique IDs／terminal／options coverage，並比較 fresh head 的資料 hash、總數、revision、terminal 與 coverage。cursor 本身可輪替，沒有持久化它。
- reference／requested roles 改變、revision／首頁資料不符則重新抓該 source；auth 絕不退回舊完成 cache。短 terminal 不可借舊完整頁補成 complete。
- raw proof 會區分原 cached pages 與 stable_revision_head_v1 的 fresh verification，不把舊頁偽稱為本輪逐頁重新擷取。

跨頁 revision 的完整穩定語意是 adapter 的前提，mock 只驗這個契約；尚未證明任何正式 provider 支援它，正式 adapter 不可盲目打開旗標。

## 容量與中斷

每頁 fragment 上限 1 MiB，每 source 最多 100 片，每 job 當前進度的 fragment 上限 16 MiB、manifest 8 MiB、900 source slots；進度 metadata／單 source state 另有 4 KiB／32 KiB 上限。較早 observation 的歷史根不在這個 active 上限內，整體歷史容量與 job cleanup 仍需後續保留政策。超額進 capacity_blocked，保留最後有效進度，不產生半份 result。

每頁保存前及重建 cached pages 都有合作式 execution check；取消／過期 generation 不能更新進度。中斷前已保存頁可追溯，尚未掛入有效進度的新 immutable blob 只能視為待清理物件，不能授予發布資格。這是本地 SQLite 契約，不等於 D1 或正式 API 的 resume 保證。

local_tests/test_collection_checkpoint.py 二十項案例涵蓋持久頁／脫敏、重啟沿用、未承諾 revision 全重抓、改版／auth／role scope／短頁、cursor 輪替、錯誤 checkpoint、fragment／mutable state hash、retention roots、預算中斷／取消／容量、線性 blob 策略、歷史觀測與 SQLite trigger 故障時的原子回滾。

collection inputs 可固定 member_epochs；collector 在任何 port 呼叫前驗證，raw／training／原子 observation 必須保留相同 map。名單重新加入的 epoch 不符不得沿用舊 training，詳見 82。
