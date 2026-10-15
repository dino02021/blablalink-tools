# 96：凍結 GS 模板表頭對照與差異

## 證據來源與邊界

本批只讀本地 nikke-union-management 的 healthcheck_sync.gs 原始碼，不讀或呼叫試算表。`local_tests/fixtures/gs_header_provenance.json` 記錄來源 Git head、文字 SHA-256、十個函式行範圍與 fixture hash。fixture 固定十二欄模板、九／八欄名單、屬性順序、詞條縮寫及實際模板表頭建構函式；測試不依賴日後外部 checkout 的狀態。

Node VM 沒有 require、UrlFetchApp 或 PropertiesService，禁用動態 code generation。僅注入 mock Sheet 的 getRange／clearContent／setValue／merge／column width 與無副作用格式方法，以及 BorderStyle 常數。舊函式實際寫入 synthetic grid，再與 Python typed view 比對；不是重新手寫一份預期結果來冒充 GS。

## 已核對的契約

- 名單九欄與移除評分追蹤的八欄順序；五屬性順序；全部九種詞條縮寫字典。
- 單角色標準表十二欄的角色名、群組標籤、詞條標籤，以及角色／技能／裝備／詞條／footer 的實際合併座標。
- 空詞條、三種各三項縮寫組合、未知詞條混合已知詞條；名稱以公式樣式字串保留為 literal。
- Python 十二個穩定 metric ID 的實體順序，trait function ID 不因顯示標籤而改變。

GS `_hcSetStandardTemplate` 不負責上一層屬性 header；該層不能從本批宣稱逐格相同。舊 footer 只建合併範圍，本批也不把這當成門檻／係數／權重值的完整核對；數值證據仍使用既有 scoring／standard round-trip。

## 明確差異

1. 練度表第二列：GS 是突破／技能／裝備／收藏品／詞條的合併群組；Python 是逐個 metric／function label，保留隱藏 composite member ID。角色 title 和十二欄語意順序可對應，但不能原格覆蓋後仍假稱舊表頭相容。切換應使用已有明確 grid migration 契約。
2. 顯示字典未標準化：例如「 攻擊力 」在 GS 經 trim／比較 key 縮寫為「攻」，Python 現在保留原 label，標準表顯示「詞條 ( 攻擊力 )」。這是 presentation 差異，function_0 身分不變；本批不修改顯示字典或偷偷清洗原 label。

## 未完成範圍

七項 `local_tests/test_gs_header_contract.py` 案例提供上述有界證據，未完成完整舊工作簿盤點。評分結果三層表頭／nickname cache、跨屬性或同角色多類型舊模板、舊人工區 mapping、實際既存 merge／範圍／保護與正式字典語意仍須另處理。不要移除驗收表中的完整舊 header 缺口，也不要將本批歸類為正式工作簿驗收。
