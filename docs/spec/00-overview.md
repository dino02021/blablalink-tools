# 概述

## 專案職責

`BlaBlaLinkTool` 是一套內部 Python 工具鏈，服務於三個工作流群組：
- BlaBlaLink 登入與會話管理
- 聯盟突襲下載與上傳
- Nikke 角色詳情與同步等級上傳

相同的業務邏輯必須能從以下管道重複利用：
- 桌面 GUI
- CLI
- GitHub Actions
- 外部雲端排程器

## 架構規則

GUI 只負責互動與狀態顯示。

下載、上傳、路由與批次編排應該存在於共用模組，例如：
- `lib/jobs/`
- `lib/api.py`
- `lib/data_store.py`
- `lib/google_sheets/`

## 目錄與責任分工

### 入口點
- `main.py` / `main.pyw`：應用啟動入口，只負責啟動 [lib/gui_main.py](../../lib/gui_main.py) 的 `LoginApp` 與日誌記錄。

### GUI 基礎設施
- [lib/gui_main.py](../../lib/gui_main.py)：主 GUI 視窗（`LoginApp` class）、登入按鈕、主要行動按鈕。子對話框由 `dialogs` 模組提供。
- [lib/gui/layout.py](../../lib/gui/layout.py)：共用視窗工具：
  - `create_dialog()` / `close_dialog()` 確保一致的模態行為
  - `place_on_same_screen()` 用於視窗定位
  - 皆使用 `grab_set()` + `wait_window()` 模式
- [lib/gui/ui_constants.py](../../lib/gui/ui_constants.py)：統一 UI 常數（padding、間距、元件尺寸）。

### 對話框模組
- [lib/gui/dialogs/accounts.py](../../lib/gui/dialogs/accounts.py)：帳號選擇與管理
- [lib/gui/dialogs/sheets.py](../../lib/gui/dialogs/sheets.py)：試算表類型與對應管理
- [lib/gui/dialogs/union_raid_upload.py](../../lib/gui/dialogs/union_raid_upload.py)：聯盟突襲上傳對話框（子視窗遵循相同佈局規則）
- [lib/gui/union_raid_download.py](../../lib/gui/union_raid_download.py)：聯盟突襲下載流程
- [lib/gui/dialogs/](../../lib/gui/dialogs/) 其他對話框遵循相同佈局原則。

### 外部 API 與會話
- [lib/api.py](../../lib/api.py)：主要 API 端點與路由
- [lib/auth.py](../../lib/auth.py)：認證與會話管理
- [lib/utils.py](../../lib/utils.py)：工具函式

### 資料與運行時
- [lib/data_store.py](../../lib/data_store.py)：資料讀寫與轉換；data 類檔案來源為雲端 GitHub repo
- [lib/constants.py](../../lib/constants.py)：全域常數與配置
- [lib/log.py](../../lib/log.py)：日誌工具
- [lib/github_data_sync.py](../../lib/github_data_sync.py)：直接存取 `BlaBlaLinkTool-data` 的資料層，GUI/CLI 不再保留本地 data bootstrap
- [.github/runtime/](../../.github/runtime/)：排程工作配置檔

### Google Sheets 整合
- [lib/google_sheets/service.py](../../lib/google_sheets/service.py)：Sheets API 服務層

## 主要模組

### 入口點

- [main.py](../../main.py)
- [main.pyw](../../main.pyw)
- [lib/gui_main.py](../../lib/gui_main.py)
- [lib/cli/run_job.py](../../lib/cli/run_job.py)

### API 與會話

- [lib/api.py](../../lib/api.py)
- [lib/auth.py](../../lib/auth.py)
- [lib/utils.py](../../lib/utils.py)

### 資料與運行時

- [lib/constants.py](../../lib/constants.py)
- [lib/data_store.py](../../lib/data_store.py)
- [lib/github_data_sync.py](../../lib/github_data_sync.py)
- [.github/runtime/](../../.github/runtime/)

### GUI

- [lib/gui/](../../lib/gui/)
- [lib/gui/dialogs/](../../lib/gui/dialogs/)

### Headless 工作

- [lib/jobs/](../../lib/jobs/)

### 雲端排程器

- [cloudflare-scheduler/](../../cloudflare-scheduler/)

外部排程器現在是生產環境的時間自動化主要路徑。
GitHub 原生 `schedule` 不是此儲存庫的生產時間配置的唯一真相來源。

## 支援的工作流

### 聯盟突襲

- 下載聯盟突襲 JSON
- 依 `union_id` 路由上傳
- 支援 `single` 與 `all`
- 支援 `download` 與 `full`
- 事件日期確定後允許一次性排程執行

### Nikke

- 從目前會話取得聯盟成員
- 下載整個聯盟的角色詳情
- 依 `guild_id -> CharacterDetails` 路由上傳
- 支援 `single` 與 `all`

### 同步上傳

- 取得 `nickname -> synchro_level`
- 依 `guild_id -> CharacterDetails` 路由上傳
- 支援 `single` 與 `all`

## 真實來源規則

- `union_id` 與 `guild_id` 是路由鍵值
- `sheet_name` 僅作顯示，不是路由鍵值
- `account` 選擇會話身份，不是試算表目標
- 雲端排程規則存放在 `.github/runtime/scheduler_config.json`
- 工作流檔案保持為手動分派入口與執行包裝

## 開發優先順序

行為變更時，保持：
- GUI、CLI 與自動化間的共用邏輯
- 穩定的 `JobResult` 結構（實踐中）
- 路由規則集中在工作層
- 配置驅動的排程，而非硬編碼的雲端 cron 行為
