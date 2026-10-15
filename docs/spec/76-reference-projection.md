# Reference 變更的明確衍生養成投影

application/reference_projection.py 的 project_reference_training 讀取固定 raw 與 source／target reference documents；source 版本必須等於原 raw reference，兩份 reference scope 相同且適用於 raw 的聯盟。code SHA 與三個來源 ID 都納入固定 plan_id。

## 可以沿用與必須停止

- 原 raw 必須有和 source reference 完全相同的 reference_role_ids。
- target 角色只可為 source 的子集合，保留相同穩定 ID；順序／別名／標籤等 metadata 改動不會改變養成數值。
- target 的每一個 prefix→function ID 必須和 source 相同；可以移除既有 prefix，不能新增或重定義。
- 新角色回 ownership_refresh_required；新增／改對應的 function 回 function_mapping_refresh_required；缺 universe 回 role_coverage_unproven。以上皆不產生 training，不能假稱已補抓。
- auth_required 回 blocker，不可藉 reference 變更提升為可用來源。最多 300 raw 成員，reference 本身仍使用既有字典大小上限。

## 來源不變，衍生物可追溯

先用 source role universe／prefix map 走既有 normalizer，再依 target 角色與 function IDs 篩選 TrainingRecords。新 training 使用 target reference version，但 original raw 完全不改；payload 明記 raw_snapshot_id、source／target reference document ID、source reference version、source training hash、normalizer version 與 code SHA。

reference 相容只表示可以做 schema／字典投影，不等於資料完整。unknown metric／ownership 保持 unknown，仍須 calculate_healthcheck 的同一份完整性判斷。verify_reference_projection 從固定輸入重建並核對整份 plan 與 training，不能換 code 或內容。

目前是純本地規劃／衍生 API，不自動寫 object store、推 head、改 config、排補抓或發布。需要保存時，由可信呼叫者保存 raw／兩份 reference／衍生 training；training 的 lineage 讓現有 retention 沿引用保留原始來源。若再改字典，仍從 raw 原本 reference 直接核對最新 target，不能將中間 training 當作重新抓取的 raw。

補充執行限制：normalizer 每個 member／role 都呼叫既有 execution check，使大量重投影也遵守 worker deadline／operation／memory budget，數值與 schema 版本不變。

local_tests/test_reference_projection.py 十項案例涵蓋別名更新、移除角色／prefix、三種不相容變更、缺 universe、unknown 不升級、code／plan/source 置換、auth 及 lineage retention。尚未有跨 reference 補抓 executor 或正式 provider 相容性保證。

相容投影保留 training 的 member_epochs；reference 相容不能重設或補造加入版本。詳見 82。
