# Runtime And Data

## Data Categories

專案內的資料分成四類：

### 1. Repo Static Data

位於 `data/`，屬於靜態 lookup 或前置整理資料。

目前重要檔案：
- `nikkes_info.json`
- `nikkes_nickname.json`
- `function_names.json`
- `nikke_checklist_name_codes.json`

用途：
- Nikke 名稱對照
- checklist 預處理
- 角色細節欄位整理

### 2. Runtime Settings

屬於程式執行時需要的設定檔。

重要檔案：
- `accounts.json`
- `sessions.json`
- `service_account.json`
- `google_sheets_id.json`
- `union_account_map.json`

### 3. Generated Output

由 API 擷取後產生的輸出檔。

重要目錄：
- `UnionRaid/`
- `CharacterDetails/`

### 4. Workflow Mapping

供 workflow 使用的 mapping 檔。

重要檔案：
- `.github/runtime/union_google_sheets_id.json`
- `.github/runtime/nikke_google_sheets_id.json`

## Runtime File Contracts

### `accounts.json`

用途：
- 儲存帳號清單與 GUI 可選帳號。

實作備註：
- 程式目前仍有歷史格式相容處理。
- 修改時不要假設每個欄位都一定存在。

### `sessions.json`

用途：
- 保存 Playwright `storage_state`。

規則：
- job layer 透過 `account` 找到對應 session。
- session 缺失時，所有需要登入的流程都應直接失敗。

### `service_account.json`

用途：
- Google Sheets 認證。

規則：
- 只提供給 Google Sheets client 使用。
- 不應被業務邏輯直接解析。

### `google_sheets_id.json`

用途：
- 儲存 GUI runtime 使用的 sheet mapping。

有效欄位以實作為準，核心欄位至少包含：
- `type`
- `sheet_name`
- `sheet_id`
- `union_id`

路由規則：
- `type=UnionRaid` 給 union raid upload 使用。
- `type=CharacterDetails` 給 nikke upload 與 sync upload 使用。
- `union_id` 為必要路由欄位。

範例：

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

用途：
- 定義 `union_id -> username` 的關係。
- `all` 模式 pipeline 由這份資料決定要跑哪些帳號。

規則：
- `username` 是登入帳號識別。
- `union_id` 是 union raid routing 用資料，不是執行帳號主鍵。
- pipeline 會對 `username` 去重後逐帳號執行。

範例：

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

## Generated Output Contracts

### Union Raid JSON

檔名由時間戳與 `union_id` 組成。

用途：
- 作為 upload 階段的唯一輸入。

規則：
- upload 以檔案內容的 `union_id` 做 routing。
- 不應依賴檔名解析 `union_id` 作為唯一來源。

### Character Details JSON

每個檔案代表一位成員的角色細節。

規則：
- 檔名目前以 `nickname` 為主。
- 檔案內容必須能提供 `nickname` 與 `character_details`。
- upload 可以指定只上傳本次成功下載的 `files`。

## Checklist Preparation

[lib/data_store.py](../../lib/data_store.py) 會把：
- `nikke_checklist_name_codes.json`
- `nikkes_info.json`

整理成 `checklist_items`，供 Nikke batch download 過濾角色列表使用。

這層規則很重要：
- `fetch_user_characters()` 回傳的是角色 code 集合。
- `prepare_checklist_items()` 產出的資料必須能用來過濾這些 code。

## Routing Rules

### Union Raid Upload

- 從下載結果或資料檔內容取得 `union_id`。
- 只匹配 `type=UnionRaid`。
- 再用 `union_id` 精準匹配 target sheet。

### Nikke Upload

- 先從 account 對應的 session 取得 `union_id`。
- 只匹配 `type=CharacterDetails`。
- 再用 `union_id` 精準匹配 target sheet。

### Sync Upload

- 先從 `fetch_guild_members()` 取得 `guild_id`。
- `guild_id` 視同 `union_id` 使用。
- 只匹配 `type=CharacterDetails`。

## Priority Rules

修改資料來源時，維持以下優先序：
- 業務路由規則優先寫在 job layer。
- GUI 只讀寫 runtime 檔，不自行定義另一套 routing 規則。
- workflow mapping 與 GUI runtime mapping 若分離，必須明確知道哪個流程讀哪一份。

## Out Of Scope

以下不屬於這份開發規格：
- 外部平台設定步驟
- 憑證建立教學
- 人工維運 checklist
