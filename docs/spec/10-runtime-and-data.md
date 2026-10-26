# 運行時與資料

## 資料分類

### 1. 儲存庫靜態資料

實際來源為 `BlaBlaLinkTool-data` GitHub 儲存庫。

重要檔案：
- `nikkes_info.json`
- `nikkes_nickname.json`
- `function_names.json`

用途：
- Nikke 查詢表
- 角色名稱、代碼與詞條 ID 字典；健檢名單設定另以共用試算表為準
- 角色詳情正規化

規則：
- 程式運行時不應直接依賴本地 `data/` 目錄
- 讀取必須經由雲端資料層，直接從 `BlaBlaLinkTool-data` 取得
- 寫入必須直接推送到 `BlaBlaLinkTool-data`
- GUI 啟動與 CLI job 執行前不需要先同步本地 `data/`；資料會在實際讀取時直接向雲端抓取

### `function_names.json`

目的：
- 作為健檢詞條下拉選單與練度資料擷取的 function id 對照表。

規則：
- 每筆資料只包含 `function_name` 與單一整數 `function_id`。
- `function_id` 由來源 `state_effect_id_list[0]` 的前 5 碼轉成整數。
- 只保留 `state_effect_id_list[0]` 以 `700` 開頭的資料。
- 不支援舊格式 `function_id_list`；雲端舊資料不合併、不轉換。
- 產生內容與雲端完全相同時，不應重複上傳。

例子：

```json
[
  {
    "function_name": "【優越代碼傷害增加】",
    "function_id": 70005
  }
]
```

### 2. 運行時設定

重要運行時檔案：
- `accounts.json`
- `sessions.json`
- `service_account.json`
- `google_sheets_id.json`
- `union_account_map.json`

### 3. 生成的輸出

輸出目錄：
- `UnionRaid/`
- `CharacterDetails/`

### 4. 工作流運行時對應

`.github/runtime/` 底下的檔案供工作流與自動化使用：
- `union_google_sheets_id.json`
- `nikke_google_sheets_id.json`
- `scheduler_config.json`

## 運行時檔案契約

### `google_sheets_id.json`

核心欄位：
- `type`
- `sheet_name`
- `sheet_id`
- `union_id`

路由規則：
- `type=UnionRaid` 用於聯盟突襲上傳
- `type=CharacterDetails` 用於 Nikke 上傳與同步上傳
- `union_id` 為路由必填

例子：

```json
[
  {
    "type": "UnionRaid",
    "sheets": [
      {
        "sheet_name": "Example Union Raid",
        "sheet_id": "example-union-raid-sheet",
        "union_id": "example-union-a"
      }
    ]
  },
  {
    "type": "CharacterDetails",
    "sheets": [
      {
        "sheet_name": "Example Character Details",
        "sheet_id": "example-character-sheet",
        "union_id": "example-union-a"
      }
    ]
  }
]
```

### `union_account_map.json`

目的：
- 對應 `union_id -> username`
- 定義 `all` 模式包含哪些帳號

規則：
- `username` 是登入帳號鍵值
- 管線依 `username` 去重複
- `union_id` 仍用於聯盟突襲路由

例子：

```json
[
  {
    "union_id": "example-union-a",
    "username": "example-account-a"
  },
  {
    "union_id": "example-union-b",
    "username": "example-account-b"
  }
]
```

### 共用試算表的 `健檢名單`

目的：
- 作為 Python 與 Apps Script 唯一的健檢角色與詞條設定來源
- 作為 Nikke 批次下載的 checklist 來源

規則：
- 來源預設為共用設定試算表 `167RcdRZu8OY5Im9wqWHDBUjZAiENJvv7mvFD74ZXzgU`；可用 `BLABLALINK_COMMON_CONFIG_SPREADSHEET_ID` 覆寫，必須與 Apps Script 的共用設定表一致。
- A-F 表頭固定為 `名稱`、`屬性`、`類型`、`詞條1`、`詞條2`、`詞條3`。
- 角色名稱經空白、全半形及冒號正規化後，必須唯一對應 `nikkes_info.json` 的 `name_code`。
- `type` 只允許 `輸出` 或 `輔助`
- `element` 讀取試算表 B 欄，允許值固定為 `電擊`、`水冷`、`燃燒`、`風壓`、`鐵甲`；新增時可使用角色字典預設屬性。
- 可選欄位為 `function1`、`function2`、`function3`，對應健檢表詞條 1/2/3
- `function1`、`function2`、`function3` 儲存的是正規化後的 function 名稱，不是 id
- function 名稱正規化規則：移除空白與括號類符號，並移除結尾 `增加`
- 同一 `name_code + type` 不可重複
- 下載 checklist 仍只使用 `name_code`，不使用 `type` 參與路由
- 詞條正規化後必須完整匹配 `function_names.json` 的名稱，不使用包含文字的模糊比對。
- 無法辨識的角色、屬性、類型、詞條或重複設定必須明確失敗，不可略過。
- 同一角色同時存在輸出與輔助設定時，兩筆的詞條順序必須一致。
- GitHub `nikke_healthcheck_members.json` 不再參與執行時讀寫，也不作為失敗時的降級來源。

例子：

```json
[
  {
    "name": "範例角色",
    "element": "水冷",
    "type": "輸出",
    "function1": "優越代碼傷害",
    "function2": "攻擊力",
    "function3": "最大裝彈數"
  },
  {
    "name": "另一位範例角色",
    "element": "燃燒",
    "type": "輔助"
  }
]
```

### `scheduler_config.json`

目的：
- 雲端排程的真實來源
- 可從 GitHub UI 或本地 git 工作流編輯
- 由 Cloudflare Worker 排程器使用

核心欄位：
- `version`
- `timezone`
- `jobs`

支援的工作類型：
- `daily`
- `once`
- `interval`

`daily` 欄位：
- `name`
- `enabled`
- `type`
- `time`
- `workflow`
- `ref`
- `inputs`
- 選用 `days_of_week`

`once` 欄位：
- `name`
- `enabled`
- `type`
- `date`
- `time`
- `workflow`
- `ref`
- `inputs`

`interval` 欄位：
- `name`
- `enabled`
- `type`
- `start_date`
- `time`
- `interval_minutes`
- `workflow`
- `ref`
- `inputs`

`interval` 語義：
- 代表從 `start_date + time` 開始的 24 小時視窗
- 起點時間包含在內
- 終點時間包含在內
- 例如 `start_date=2026-04-16`、`time=04:00`、`interval_minutes=10`
  代表執行區間為 `2026-04-16 04:00` 到 `2026-04-17 04:00` 含當下
  最後一次合法觸發是 `2026-04-17 04:00`
- 間隔計算必須以 `start_date + time` 為基準，不可直接用當前分鐘對 `interval_minutes` 取模

例子：

```json
{
  "version": 1,
  "timezone": "Asia/Taipei",
  "jobs": [
    {
      "name": "nikke_pipeline_daily",
      "enabled": true,
      "type": "daily",
      "time": "18:00",
      "workflow": "nikke_pipeline_run.yml",
      "ref": "main",
      "inputs": {
        "scope": "all"
      }
    },
    {
      "name": "union_raid_pipeline_once",
      "enabled": true,
      "type": "interval",
      "start_date": "2026-12-31",
      "time": "04:00",
      "interval_minutes": 5,
      "workflow": "union_raid_pipeline.yml",
      "ref": "main",
      "inputs": {
        "mode": "full",
        "scope": "all",
        "month": "2026/12"
      }
    }
  ]
}
```

## 生成的輸出契約

### 聯盟突襲 JSON

- 上傳路由使用檔案內容 `union_id`
- 檔案名稱不是唯一路由來源

### 角色詳情 JSON

- 每個成員一個檔案
- 檔案內容必須提供 `nickname` 和 `character_details`
- `character_details[].functions` 固定輸出三筆，順序對應健檢名單 `function1`、`function2`、`function3`
- `functions[].function_id` 來自 `function_names.json.function_id`；找不到對應詞條時該位置留空
- `functions[].function_value` 取裝備詞條加總值，來源值若為負數必須取絕對值後再輸出
- 上傳可能只針對選定的 `files`

## 路由規則

### 聯盟突襲上傳

- 依 `type=UnionRaid + union_id` 路由

### Nikke 上傳

- 依 `type=CharacterDetails + union_id` 路由

### 同步上傳

- 依 `type=CharacterDetails + guild_id` 路由
- `guild_id` 與 `union_id` 視為相同路由域

## 排程規則

- 雲端排程定義必須存放在 `scheduler_config.json`
- GitHub 工作流檔案不應是生產時間排程的主要來源
- 編輯排程行為表示需要同時編輯 worker 邏輯與規格（若契約改變）

## 範圍外

本規格不涵蓋：
- 雲端帳號設定教學
- Token 建立教學
- 手動操作檢查清單

## 健檢同步補充規格

- 共用試算表的 `健檢名單` 是唯一健檢角色與詞條設定來源。
- GUI 名單新增與編輯直接儲存到共用表；既有角色在原列更新 A-F，G 欄以後的追蹤、暱稱與優先度不覆蓋。
- 刪除角色時刪除共用名單中對應整列，其他角色的 G 欄以後資料隨原列移動；新增角色追加新列。
- 儲存前核對讀取時的名單內容，若名單已被他人修改則拒絕儲存。
- `健檢名單` worksheet 的 Python 寫入欄位固定為 A-F：
  - A：名稱
  - B：屬性
  - C：類型
  - D：詞條1
  - E：詞條2
  - F：詞條3
- Python 端只更新 `健檢名單` 的 A-F 範圍；G 欄以後的既有儲存格不得被覆蓋。
- `run_healthcheck_upload` 保留呼叫入口，行為改為唯讀核對目前帳號的目標表角色與詞條欄位，不再送出另一份名單覆寫共用來源。
- 每次下載與上傳共用同一份 `HealthcheckSnapshot`；來源名單與目標表頭內容指紋寫入 `CharacterDetails/*.json.healthcheck_context`。
- 練度檔另含 `expected_name_codes`，記錄該成員本輪應下載的完整角色集合；API 與檔案內容必須完整且不重複。缺少此欄位的舊檔須重新下載。下載工作回傳本輪成功下載的 `expected_members`、`failed_members`、`skipped_members` 與 `is_upload_ready`；缺人時繼續上傳成功且完整的成員，失敗成員的既有資料保留，不使用舊檔補上。已列入成功清單的檔案若在上傳前遺失，仍停止整批寫入。結果保留原始聯盟總人數與失敗名單，詳見[工作契約](30-headless-jobs-and-actions.md)。
- JSON 角色資料包含 `name_code`，上傳前核對角色及三個 `function_id` 的順序。
- 下載前與寫入前檢查角色區塊為 12 欄、分類位置與詞條縮寫，雙屬性角色 `拉毗：小紅帽` 同時核對燃燒及鐵甲表。
- 名單或表頭已變更、舊檔缺少來源紀錄、檔案屬於其他試算表時，停止上傳並提示重新下載；欄位不一致時提示先執行試算表內的健檢嚴格同步。
- Python 端不得直接新增、刪除或格式化 `角色練度_{element}`、`角色練度_變化`、`練度標準`、`練度評分結果` 的角色欄位，避免與 gs 端同步流程重複操作。
