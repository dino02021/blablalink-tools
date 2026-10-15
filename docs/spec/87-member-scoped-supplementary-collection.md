# 依固定缺口計畫補抓成員養成

application/supplementary_collection.py 提供可信本地 Python 呼叫入口，使用既有 CollectionPort／MockCollection 與持久分頁。沒有新增 HTTP、正式 Session、背景排程或 worker transport 接線。

## 固定需求與範圍

prepare_supplementary_inputs 從 content-derived reference、config、base raw、目前固定 union_inputs 與 code 重建 74 的 reuse plan。ready、auth／reference 等 blocker、沒有可執行 request 均不建立補抓輸入。固定 plan ID／完整 plan、來源 IDs、role universe、function map、成員子集與加入 epoch。

補抓粒度目前為成員：只選有 request 的成員，為每位重新抓 ownership、details、effects，完整驗證依賴；不宣稱已做到 role／option 細粒度 provider 請求。其餘成員不呼叫 port。原 raw 最多 16 MiB，config／union inputs 各最多 8 MiB。

supplementary_task_spec 為 refresh_roster_and_training 明確意圖工作，固定 collection_inputs、union_inputs、config、base raw 與 reference；config 須為已發布版本。executor 在 fetch 前重新建立完全相同輸入與 task，不能修改 request subset、epoch、code 或 plan 後沿用舊授權。

## 觀測與合併

只對成員子集建立 CollectionCheckpoint，沿用頁面白名單、revision／scope、opaque cursor 不落地、容量、lease 與 retry 限制。每輪先原子保存 fresh subset 的 raw／training／完整 checkpoint observation；這份觀測不冒充全員都是新抓取。

auth 直接停在 auth_required，不 fallback 舊 ownership 或數值。partial／failed source 停在 retry_wait，不生成 ready 結果。原分頁及歷史觀測保留，下一次有界執行仍驗證相同固定需求。

所有 fresh source 完成後，以 composite ID 將這些成員的整份 raw 取代，不能用舊值蓋掉新觀測的 not_owned／未知。只保留固定現有名單；尚缺且未在本次 request 的成員仍為缺口，不補造空白或未持有。

merged raw 是新 immutable 物件，最多 16 MiB，不修改原 raw、不重標 reference。supplementary_lineage 固定 base／fresh raw／union inputs，逐成員記錄來源 raw ID 與來源文件 captured_at。merged captured_at 為組合這次觀測的時間，不代表保留的舊成員也在這次刷新；若來源已是合併物件，可沿 raw IDs 回查原來源鏈。

新加入 epoch 只能由本次固定成員完整觀測傳入；保留成員沿用其原 epoch，相容 reference 規則不變。training 綁定 merged raw，因此來源與快照仍可回溯。

## 結果與執行資格

完成 source observation 的 succeeded 不等於所有養成需求已滿足。result 明列 merged／fresh／base IDs、checkpoint、對固定 config／名單的 is_training_ready、完整 followup plan／ID。若仍有別人成員或欄位缺口，顯式回傳後續計畫，不在同一固定工作偷偷擴大抓取範圍、不自動發布。

加入版本先驗可能使初始計畫只看到重新加入者；其補抓完成後，下一個計畫才揭露其他原有缺口。這種情況以 succeeded observation + is_training_ready=false 表示，由後續明確工作決定下一輪。

執行前若綁定名單已過期，supersede_if_stale 原子終止該 lease，不建立假結果、不呼叫 port。執行中名單變更仍由 complete_compute 的 current fence 阻擋目前結果。config／reference 固定為該次歷史需求，不能把其 ready 旗標解讀成已滿足後來新採用的設定。

合作式 time／memory／operation 上限及 checkpoint 沿用既有契約。沒有把此入口接到正式 provider，也未加入本地 worker 的 collector transport；目前以直接 mock port 執行驗證。

## 本地證據與尚缺

local_tests/test_supplementary_collection.py 的 16 項案例驗證只抓一人三個 source、舊來源時間保留、完整 not_owned 取代舊持有、auth／partial／retry 與歷史、改 plan／subset／epoch／code 前拒絕、實際離開／重新加入後的新 epoch、followup 未 ready、預算、retention、raw scope／reference／身分拒絕及 stale 名單 fetch 前終止。

尚需本地 worker 的 mock collector transport、名單／設定採用後補抓調度、role／option 粒度協作、hosted 契約及真實 provider 完整性。正式試算表與憑證均未使用。
