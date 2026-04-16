# 運行時與資料

## 資料分類

### 1. 儲存庫靜態資料

存放於 `data/`。

重要檔案：
- `nikkes_info.json`
- `nikkes_nickname.json`
- `function_names.json`
- `nikke_checklist_name_codes.json`

用途：
- Nikke 查詢表
- 檢查清單準備
- 角色詳情正規化

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
