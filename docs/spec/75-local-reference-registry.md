# 本地 reference 候選與 CAS 發布

application/reference_catalog.py 以既有 union_inputs snapshot envelope 保存 reference_kind=healthcheck_reference、reference_schema_version=1，無須另造不相容 Document kind。

## 不可變內容版本

reference payload 包含有序 role IDs、五位數 prefix→function ID、角色／function 別名、雙屬性例外。StandardReference／LegacyCatalog 共用既有 identity、唯一性、別名 target 與元素驗證；最多 1000 角色／1000 prefixes／10000 aliases、canonical payload 2 MiB。

reference_version 是完整 payload 的 SHA-256，不是可重用的人工名稱。captured_at 不影響此版本，但影響 snapshot_id；同內容不同擷取時間可追溯為同一字典版本。read_reference 重新建立並核對完整 document，拒絕偽造版本、未知 schema 或欄位。

## SQLite 候選與發布

0009 migration 新增 reference_candidates／heads／publications／publication_requests，全部與原 coordinator 共用 SQLite objects 和 migration checksum。

LocalReferences.create_reference_candidate 固定 source document、base reference ID 與 validation code SHA；同 scope／內容版本／base／code 的重複擷取共用原候選。新候選要求 base 仍為現有 head，保存候選不動 head。

publish_reference 要求明確 confirmed candidate、expected base、actor 與相同 code，於 BEGIN IMMEDIATE 交易中檢查 CAS、保存不可變 publication receipt／request 去重與更新 head revision。兩個連線從相同 base 發布不同候選只允許一個成功。

同 request 不可換 intent，同候選不可改 actor／code 重發；完整重播可回原 receipt，即使 head 後來改變也不把 head 倒回。候選與 receipt 每次讀取都核對 document／signature／semantic identity。這是可信本地 API，不是已串好的用戶權限或遠端 endpoint。

## 其他版本不會被偷偷替換

發布 reference 不發布 healthcheck config、不重新標記 raw、不排 collection／compute job、不寫試算表。舊 config／raw 仍固定舊 reference_version；新 config 與舊 raw 不符時，reuse planner 回 reference_reconciliation_required。即使僅 alias 改動，目前也採這個保守邊界；後續可用明確相容性證據建立衍生投影，不能直接改原 raw。

候選、head、舊 publication 都是 retention 根；原掃描所有非 objects table 的保留策略會保護這些 reference documents。備份依既有 SQLite backup 與 migration manifest 一起保存，不另外建立資料庫或服務。

local_tests/test_local_references.py 的 13 項本地案例涵蓋內容版本、時間去重、重啟、原 receipt 重播、五類確認變更、兩連線 CAS、新版本不重標 raw、prefix 驗證與 reference 保留根。沒有讀取遠端字典或連線正式環境。
