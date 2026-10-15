# 舊版標準格與同角色多類型

adapters/legacy_standard.py 只解析明確傳入的 mock grid。來源契約核對本地 GS healthcheck_sync.gs、utils.gs、score_engine.gs，以及 Python lib/data_store.py／healthcheck_config.py；未讀取正式工作簿。

## 橫向標準與名單

- inputs.legacy_checklist 為第一列 header 與後續名單列；支援九欄格式及缺少 G 評分追蹤的舊八欄格式。
- 舊八欄的追蹤預設明確為停用，不自行猜測開啟。空白列只略過，角色未填但其他必要欄有值則拒絕。
- LegacyCatalog 固定 reference、角色／function 別名與雙屬性例外。名稱以 NFKC、移除空白／零寬字與冒號正規化後唯一對應 ID。
- 可加入保護的角色ID 欄消除重名；已知名稱與 ID 矛盾時拒絕，未知顯示名稱可依保護 ID 改名。
- inputs.legacy_standard 至少六列；B 開始每角色固定十二欄。row1 屬性、row2 角色、row3 分組、row4 門檻、row5 係數、row6 總分權重。
- 分組 offset：0 突破、1 技能、4 裝備、8 收藏品、9 詞條；缺突破的舊十一欄或破損寬度須先預覽結構遷移，不能硬套。
- standard_column_count 可提供實際欄數；不足十二欄拒絕。標題之外還有未登記資料也拒絕，不忽略可能的人工區。
- standard_role_ids 可保存起始實體欄（1-based 字串）對應的穩定 ID；不允許覆蓋相互矛盾的已知標籤。
- 詞條按名單 slot 與 pinned function ID 建立；未選詞條卻填有門檻／係數，即使為 0 也拒絕。
- 合併權重格可只在首格有值；重複非空值必須一致。雙屬性副本也必須是同一份門檻、係數與權重，衝突不得任選。

## Healthcheck model version 2

盤點發現 Python 名單以 (name_code,type) 去重，允許同角色輸出／輔助；部分 GS 同步函式卻按名稱保留首項。新核心此前的一角色一類型不足以完整接收此資料，現明確採共用養成與標準。

- RoleRule 仍以唯一 name_code 綁定一份訓練資料與評分原始分。
- additional_role_types 保存其他類型，tracked_role_types 指定實際納入評分的類型；輸出追蹤和輔助追蹤可獨立停用。
- structured／legacy reader 合併同角色不同類型，但共用門檻、係數、權重、詞條、標籤與優先度必須相同；同角色同類型重複仍拒絕。
- 原始角色分只計一次，可依追蹤類型參與屬性／輔助總分；報告只產生一次共同缺項，不重複讀取養成。
- 設定 payload.model_version=2；envelope schema_version 仍為 1。舊 model v1 可讀，新擴充不得假冒 v1；部署時需搭配理解 v2 的 code release。
- 這是明確修正既有跨專案矛盾，不假稱舊 GS 每個同步入口都已支援同角色多類型。

## 證據與界線

local_tests/test_legacy_standard.py 驗證物理寬度、行列、空白列、保護 ID、重名、函式身份、雙屬性衝突、雙類型追蹤與 model v1／v2，並串接固定候選→計算→mock publisher，證明上傳標準與計算來源相同。

尚未包含 Google grid／merge metadata fetch、十一欄自動遷移、完整 merged range 驗證、寫入格式／保護區及正式字典擷取。表頭驗證只涵蓋目前列出的本地模板契約，不能代替所有正式工作簿盤點。
