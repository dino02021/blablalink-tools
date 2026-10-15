# 本地物件保留與清理計畫

adapters/local_retention.py 的 LocalObjectRetention 僅操作明確傳入的本地 coordinator DB；沒有背景 TTL、遠端刪除或正式環境接線。測試皆為臨時 mock DB。

- plan_retention(cutoff, limit) 產生不可變 ObjectRetentionPlan，含 cutoff、1..1000 刪除上限、完整 inventory／root／reachable hash 與候選 object IDs。規劃不刪資料。
- 逐物件驗 serialized SHA-256，最多 10000 物件／64 MiB；掃描非 objects 表的所有欄位，直接 object ID 或 JSON 內 ID 都作保守 root，最多 100000 rows／64 MiB root text。
- 設定、候選、任務版本／結果／checkpoint、outbox 候選／路由與其他持久引用均保留；再沿物件 JSON 引用取 transitive closure，因此 training→raw 不會因只有 training 是直接輸入而漏保留。
- 只選 created_at 嚴格早於 cutoff 且完全不可達的物件，依時間與 ID 穩定排序。所有歷史 job／candidate 仍是 root，不自動裁切歷史紀錄。
- apply_retention 要求指定完整 plan ID，BEGIN IMMEDIATE 內重新計算；任何 inventory 或 root 改變都拒絕舊計畫，整批不刪。SQL foreign key 仍是額外保護。
- 刪除與 0007 receipt 同交易提交；同計畫重播回傳原 IDs，不刪新資料。receipt 只是清理證據，不當作 active object 引用。
- 不釋放 publisher／sender 鎖、不清除 usage reservation、不把 unknown 改成已失敗。壞 hash 或超過掃描界線一律停止，不嘗試猜測可刪物件。

local_tests/test_object_retention.py 驗證先規劃／指定確認、刪除上限與去重、新引用使舊計畫過期、training→raw 鏈、已發布計算／待確認報告路由保留、時間邊界與 inventory 改變。

目前只清除 coordinator objects 的真正孤立資料；chunk／staging／backup archive、job／candidate 歷史裁切、D1 行數預算與遠端掃描仍未實作。這個 conservative 掃描假設正式輸入依既有協定以明確 JSON object IDs 引用；未來新增外部 DB 或非 JSON 引用時，必須先註冊 root／pin 契約，不能直接套用此清理器。
