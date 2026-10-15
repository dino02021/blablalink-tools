# 本地快照、報告與計算流程

目前模組：domain/snapshots.py、domain/reports.py、application/healthcheck.py。未接正式 provider。

## 快照

- Document 僅保留 canonical JSON 字串；payload／envelope 每次讀取均產生新副本。
- SHA-256 snapshot_id 綁定內容、scope、時間、schema 與 reference；payload_hash 可辨識不同時間的相同資料。
- 時間需含 timezone 並統一為 UTC。只接受 schema 1 與明列的 snapshot kind。
- restore_document 檢查 expected hash；envelope 重複 key、非有限 JSON 值及已知秘密欄位被拒絕。
- capture_training／read_training 與 capture_config／read_config 再執行各自結構與身份驗證；Document 封存本身不代表所有業務 schema 已驗證。
- MemberKey 使用 area_id 與 member_id 的 JSON pair，避免同 ID 跨區或分隔符號碰撞。
- 訓練資料與詞條保留為不可變 pair tuple；詞條以 function_id 保存，不按顯示槽位保存身份。

## 人工修正與差異

- Override 必須指定目前涵蓋的成員、角色、metric／function。
- set、clear、is_enabled 分開；set 0 與 set None 都保留明確語意。
- 同一發布集合不能重複指定相同目標；修正不改寫原快照或建立 ownership。
- diff_training 比較相同 scope 與 reference；只對共同、可比較的欄位產生變化。
- unknown、資料補齊或格式差異不作進步；確認 not_owned→owned 另列 ownership change。

## 報告與完整性

- build_report 使用 RoleAssessment 的 passed／failed／unknown，而非任何格子顏色。
- not_owned 產生「請抽取」；missing／unknown 產生 warning 與 is_complete=False。
- 技能／裝備沿用完整門檻組合，收藏品與詞條沿用 GS 格式；屬性順序固定。
- 暱稱、優先度、忽略與雙屬性由同一設定快照取得。
- 突破缺項保留於結構化檢查，文字沿用舊 GS 不輸出的規則。
- 不完整文字僅可作預覽，不能作為成功報告取代上一份有效結果。

## 計算流程

- calculate_healthcheck 驗證 scope、reference、成員唯一性及必要資料。
- 空名單或任何必要 ownership／計分值／報告值未知，拋出 IncompleteTraining。
- 確認 not_owned 的角色可正常計為 0，和資料未知不同。
- 結果綁定 config_id、snapshot_id、code_version、cohort、顯示名稱及人工修正 hash。
- 這一層不讀工作簿、不建立網路連線、不載入本機憑證，也不進行正式發布。

## 驗證

- python -m pytest local_tests -q -p no:cacheprovider。
- GS 凍結數值來源與 hash 位於 local_tests/fixtures/gs_reference_provenance.json。
- Node VM gate 只證明列出的數值 helper；不宣稱完整 GS 試算表流程已驗收。
- 目前沒有 raw API normalizer、遠端 collector 或持久任務資料庫。
