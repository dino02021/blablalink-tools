# 運行時與資料

## 資料分類

### 1. 儲存庫靜態資料

實際來源為 `BlaBlaLinkTool-data` GitHub 儲存庫。

重要檔案：
- `nikkes_info.json`
- `nikkes_nickname.json`
- `function_names.json`
- `nikke_healthcheck_members.json`

用途：
- Nikke 查詢表
- 健檢名單設定
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

### `nikke_healthcheck_members.json`

目的：
- 定義健檢表使用的妮姬名單
- 作為 Nikke 批次下載的 checklist 來源

規則：
- 每筆資料固定包含 `name_code`、`element` 與 `type`
- `type` 只允許 `輸出` 或 `輔助`
- `element` 由 `nikkes_info.json.element` 轉中文後存入，允許值固定為 `電擊`、`水冷`、`燃燒`、`風壓`、`鐵甲`
- 可選欄位為 `function1`、`function2`、`function3`，對應健檢表詞條 1/2/3
- `function1`、`function2`、`function3` 儲存的是正規化後的 function 名稱，不是 id
- function 名稱正規化規則：移除空白與括號類符號，並移除結尾 `增加`
- 同一 `name_code + type` 不可重複
- 下載 checklist 仍只使用 `name_code`，不使用 `type` 參與路由
- 練度資料擷取會用該 `name_code` 的 `function1/2/3` 依序比對 `function_names.json.function_name`，比對成功後取 `function_id`

例子：

```json
[
  {
    "name_code": 5007,
    "element": "水冷",
    "type": "輸出",
    "function1": "優越代碼傷害",
    "function2": "攻擊力",
    "function3": "裝彈數"
  },
  {
    "name_code": 5124,
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

- `nikke_healthcheck_members.json` 為唯一健檢妮姬來源；欄位契約以前文 `nikke_healthcheck_members.json` 為準。
- 健檢名單上傳目標沿用既有 `type=CharacterDetails + union_id` 路由，不新增新的 sheet type。
- 健檢名單同步寫入目標 worksheet 名稱固定為 `健檢名單`。
- `健檢名單` worksheet 的 Python 寫入欄位固定為 A-F：
  - A：名稱
  - B：屬性
  - C：類型
  - D：詞條1
  - E：詞條2
  - F：詞條3
- Python 端只更新 `健檢名單` 的 A-F 範圍；G 欄以後的既有儲存格不得被覆蓋。
- 若雲端名單比既有工作表列數少，Python 端只清空多餘列的 A-F，不刪除整列，避免影響同列其他欄位。
- Python 端只負責上傳 `健檢名單`，不呼叫 gs web app 補欄，也不直接新增、刪除或格式化其他工作表的角色欄位。
- Python 端不得直接新增、刪除或格式化 `角色練度_{element}`、`角色練度_變化`、`練度標準`、`練度評分結果` 的角色欄位，避免與 gs 端同步流程重複操作。
