# Overview

## Project Role

`BlaBlaLinkTool` 是一個以 Python 為主的內部工具專案，處理三類工作：
- BlaBlaLink 登入與 session 管理
- Union Raid 資料下載與上傳
- Nikke 角色資料與同步等級資料的下載與上傳

同一套核心流程要能同時服務：
- 桌面 GUI
- CLI
- GitHub Actions

## Architectural Rule

核心規則只有一條：

GUI 只負責互動與狀態顯示，真正的下載、整理、上傳與路由判斷應盡量集中在 `lib/jobs/` 與共用 helper，而不是散落在按鈕事件內。

## Major Modules

### Entry Points

- [main.py](../../main.py)
- [main.pyw](../../main.pyw)
- [lib/gui_main.py](../../lib/gui_main.py)
- [lib/cli/run_job.py](../../lib/cli/run_job.py)

### API / Session

- [lib/api.py](../../lib/api.py)
  BlaBlaLink / Nikke API 呼叫。
- [lib/auth.py](../../lib/auth.py)
  Playwright 登入、storage state 建立與更新。
- [lib/utils.py](../../lib/utils.py)
  session 載入、帳號解析、同步執行 coroutine。

### Data / Runtime

- [lib/constants.py](../../lib/constants.py)
  目錄、檔名、URL 與共用常數。
- [lib/data_store.py](../../lib/data_store.py)
  runtime JSON 存取與 checklist 預處理。
- [lib/github_data_sync.py](../../lib/github_data_sync.py)
  `data/` 類靜態檔同步。

### GUI

- [lib/gui/](../../lib/gui/)
- [lib/gui/dialogs/](../../lib/gui/dialogs/)

### Headless Jobs

- [lib/jobs/](../../lib/jobs/)

### Google Sheets

- [lib/google_sheets/](../../lib/google_sheets/)

### Workflow Runtime Mapping

- [.github/runtime/](../../.github/runtime/)

## Supported Flows

### Union Raid

- 下載 union raid JSON。
- 依 `union_id` 路由到對應 `UnionRaid` 試算表。
- `union_raid_pipeline` 支援 `single` 與 `all`。
- `full` 模式表示 download 後接 upload。

### Nikke

- 先取目前登入帳號的 `guild_id` 與聯盟成員。
- 批次抓每位成員的角色資料與角色細節。
- 依 `guild_id -> CharacterDetails` 路由到對應試算表。
- `nikke_pipeline` 支援 `single` 與 `all`。
- `all` 模式下若某帳號沒有對應的 `CharacterDetails` sheet，該帳號略過，不視為整體失敗。

### Sync Upload

- 取聯盟成員的 `nickname -> synchro_level`。
- 依 `guild_id -> CharacterDetails` 路由到對應試算表中的指定 worksheet。

## Current Source-Of-Truth Rules

- `union_id` / `guild_id` 是所有上傳 routing 的主鍵。
- `sheet_name` 只用於顯示，不用於路由。
- `account` 是 session 的選擇鍵，不是資料路由鍵。
- `CharacterDetails` 與 `UnionRaid` 兩種 sheet mapping 必須明確帶 `union_id`。

## Development Priorities

修改功能時優先維持：
- GUI 與 headless 共用同一套業務規則。
- routing 規則集中，不要在多個 UI 或 workflow 重複寫。
- JobResult 的輸出 shape 穩定。
- runtime JSON 格式向後相容，除非明確重構。
