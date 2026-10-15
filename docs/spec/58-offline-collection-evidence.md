# 本地蒐集分頁與完整性證據

application/collection.py 定義 CollectionPort，adapters/mock_collection.py 只重播指定合成頁面，沒有 HTTP、URL 或憑證功能。

## 分頁契約

- 固定 scope、roster、reference 與完整 role IDs，逐成員擷取 ownership→details→effects。details 只要求觀察到已持有角色，effects 只要求觀察到的 option IDs。
- 每頁回傳 member composite ID、scope、source、reference_version、source_revision、total、next_cursor、rows；details 可明確宣告 is_options_complete。
- 同一 source 的 revision 與 total 跨頁不得改變；身份不可重複、超出 pinned reference／request 或跨成員。非末頁不能空白，cursor 不可循環。
- 預設每 source 最多 100 頁、10000 列；達頁數上限或逾時只保留 partial，結構矛盾則 failed 並清除該 source rows。ownership 只有 terminal、精確 total 才 complete；details／effects 還需精確涵蓋所有 requested IDs。
- 部分 ownership 可保留已觀察 owned，未出現角色保持 unknown；只有 complete ownership 能推定 not_owned。effects 缺資料不能使未觀察詞條變零。
- auth_required 清除該 source 資料，該成員後續 source 不再請求。無效 source 不會借用其他成員的覆蓋證據。
- 所有 rows 經既有白名單後才 hash 與保存；原始 headers、未知秘密欄位不進快照。proof 保存 revision／cursor hash、白名單 rows hash、數量與 outcome，不保存不透明 cursor 原文。

## 任務與封存

- run_collection_job 只接受 refresh_roster_and_training，依固定 collection_inputs 快照讀取 members、role_ids、function_prefixes；快照須為同 scope 的 union_inputs，collection_kind=training_inputs。
- observed captured_at 由可信 runtime 明確傳入，非重新使用舊設定 capture time。所有頁面完成後產生 raw→normalized training，training 記錄 raw_snapshot_id。
- 完整且無 normalizer issues 才 complete_compute，結果固定 raw／training／inputs IDs，不自動發布 workbook 或替換下游 desired target。
- 不完整以 collection_incomplete 延遲 5 秒 retry_wait，沿用工作 max_attempts；授權失效進 auth_required，資源檢查中斷進 capacity_blocked。部分 raw／training 沒有成功 result，但已由 [80 的 immutable observation](80-durable-collection-pages.md) 固定保留；完整 history 清理政策尚待完成。

## 驗證邊界

local_tests/test_collection.py 的 19 項合成案例涵蓋正常雙頁、白名單、重複／越界／跨身份／reference／scope、revision／total 改變、cursor 循環、空中間頁、末頁數量不足、逾時、頁數／列數上限、auth 停止請求、缺 effects 與任務結果封存。

這個 port 契約仍信任 provider 對 total、revision、options coverage 的陳述；並未證明真實 API 支援這些欄位或跨 source 的原子快照。正式 adapter 若無 revision／total，必須定義可驗證的替代證據，不得填假值宣稱完整。[80](80-durable-collection-pages.md) 已加入本地 durable page evidence 與有前提的 cache 驗證；真實 roster pagination、session／API transport、遠端安全傳輸或一般 CLI collector handler 仍未接線。
