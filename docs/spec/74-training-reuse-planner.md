# 固定 raw 的沿用與補抓規劃

application/training_reuse.py 的 plan_training_reuse 是本地純規劃器，不 fetch、不排 job、不合併新舊來源、不發布設定或試算表。輸入固定 config／raw／StandardReference／function prefix map、roster／display names／overrides／code；plan_id 綁定全部內容 hash 與具體規劃結果。

## 先補角色 universe 證據

盤點發現舊 raw 只有 ownership complete，沒有記錄「complete 對哪些角色成立」。若呼叫者擴大 role_ids，可能將後來的新角色誤當未持有。

- capture_raw_training 新增可選 role_ids，明確提供時保存 reference_role_ids，且 ownership／requested role 不能超出 universe。原來未提供的 raw 仍可讀。
- collect_training 現在總是提供固定 collection input 的 role universe；最先限制 300 成員／1000 角色，超限不先發送任何來源請求。
- normalize_training 對帶有 universe 的 raw 要求完全相同的 role_ids，禁止擴張或縮減後假裝原覆蓋仍成立。
- 新 planner 對缺失或不同 universe 回 role_coverage_unproven，不把舊 raw 的空缺當 not_owned。這是可信 collector 輸入的證據契約，未實作 provider 真實完整性盤點。

## 規劃規則

1. reference version 不同回 reference_reconciliation_required，不偷改 raw 的 reference label；角色／function／prefix mapping 不足回 reference_mapping_required。
2. 以既有 normalizer 重新投影；對當前 roster 的 auth_required 回 blocker，不產生補抓請求。
3. 使用同一 calculate_healthcheck 驗證完整需求。能完成即回固定 training_id／calculation_id，requests 空；門檻變化或改選已保存詞條可以沿用同一 raw 與 training。
4. 未持有且 coverage 有證據不補抓。新成員／持有狀態未知或突破未知規劃 ownership；技能／裝備／收藏品缺值規劃 details；特定詞條 effect 缺失可按匹配 option ID 規劃 effects；整體 options coverage 不完整則規劃 details＋effects。
5. requests 按 member／source 合併、固定排序，角色／option／原因也去重排序；不依暱稱認人。人工 override 只影響投影，input hash 綁定原始 override；不改 raw。
6. 未知 metric 的來源、無法分類的缺口保留 blocker，不用「沒有請求」冒充 ready。ready 必須已有計算結果且 requests／blockers 皆空。

只讀取本輪相關角色所需欄位的缺口；不因其他未選角色的資料不足而強迫重抓。補抓結果仍需新 source revision／完整性驗證，不能直接拿 planner 的 request 當完整性證明。reference 的持久發布／CAS head 已見 [75](75-local-reference-registry.md)，跨 reference 的相容投影見 [76](76-reference-projection.md)；補抓 executor 尚未完成。

local_tests/test_training_reuse.py 十二項案例覆蓋門檻／詞條沿用、可信缺角、新人／同名跨區、指定來源／option 補抓、auth、override、reference 不符、缺 universe、拒絕擴大 universe、原始角色越界與 deterministic plan_id。

## 加入版本補充（82）

沿用計畫另綁 member_epoch_hash；目前加入版本無法由原 training 證明時，ownership 補抓理由為 membership_epoch_unproven。人工 override 不能繞過此次加入的來源證據。

87 增加成員粒度補抓 executor：有 request 的成員完整重抓三個 source，其餘沿用原來源；保存獨立 fresh observation 與 merged raw lineage。完成觀測不等於目前全部需求 ready，結果明列 followup，未自動接 worker／正式 provider。
