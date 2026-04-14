# Overview

## 專案定位

`BlaBlaLinkTool` 是一個以 Python 實作的桌面工具，同時帶有 headless job / GitHub Actions 執行能力，用來處理 BlaBlaLink / Nikke 的三類工作：

- 帳號登入與 session 維護
- 聯盟突襲資料下載與上傳
- Nikke 角色資料、同步等級資料下載與上傳

桌面模式提供人工操作與管理介面；headless 模式讓同一組核心邏輯可在 CLI 與 GitHub Actions 重用。

## 核心能力

### 1. 帳號與 session

- GUI 透過 Playwright 自動化登入 BlaBlaLink。
- 登入成功後，cookies 會被存到本機 `sessions.json`。
- Headless job 透過 `storage_state` 重建登入狀態，不必每次重新手動登入。

### 2. 聯盟突襲

- 下載聯盟突襲資料
- 依 `union_id` 對應試算表上傳
- 支援單帳號與多帳號 pipeline
- `full` 模式可自動下載後接續上傳

### 3. Nikke 資料

- 下載目前登入帳號的角色詳細資料
- 下載整個聯盟所有成員的角色資料
- 依 `union_id -> CharacterDetails` 試算表自動路由上傳
- 上傳同步等級到指定 worksheet

### 4. 雲端同步

- repo 內 `data/` 資料可同步到獨立的 cloud data repo
- `sessions.json` / `service_account.json` / `union_account_map.json` 透過 GitHub secrets 同步
- GitHub Actions workflow 可重建必要 runtime 檔後執行 headless jobs

## 主要入口

### 桌面入口

- [main.py](C:/Users/Yabi/Documents/BlaBlaLinkTool/main.py)
- [main.pyw](C:/Users/Yabi/Documents/BlaBlaLinkTool/main.pyw)

兩者都只負責啟動 [lib/gui_main.py](C:/Users/Yabi/Documents/BlaBlaLinkTool/lib/gui_main.py) 的 `LoginApp`。

### CLI 入口

- [lib/cli/run_job.py](C:/Users/Yabi/Documents/BlaBlaLinkTool/lib/cli/run_job.py)

提供下列子命令：

- `union-raid-download`
- `union-raid-upload`
- `union-raid-pipeline`
- `nikke-batch-download`
- `nikke-pipeline`
- `nikke-upload`
- `sync-upload`

### GitHub Actions 入口

- [.github/workflows/union_raid_pipeline.yml](C:/Users/Yabi/Documents/BlaBlaLinkTool/.github/workflows/union_raid_pipeline.yml)
- [.github/workflows/nikke_pipeline_run.yml](C:/Users/Yabi/Documents/BlaBlaLinkTool/.github/workflows/nikke_pipeline_run.yml)
- [.github/workflows/sync_upload.yml](C:/Users/Yabi/Documents/BlaBlaLinkTool/.github/workflows/sync_upload.yml)

## 目錄分層

### 核心程式

- `lib/api.py`
  BlaBlaLink / Nikke API 封裝、資料整理與部分輸出檔案生成。
- `lib/auth.py`
  Playwright 登入流程與 session 建立。
- `lib/utils.py`
  session / account 儲存、同步執行 async coroutine、共用工具。
- `lib/constants.py`
  所有檔案路徑、URL、型別常數。
- `lib/data_store.py`
  runtime JSON 設定檔存取與 checklist 預處理。
- `lib/log.py`
  logging 工具。

### GUI

- `lib/gui_main.py`
  主視窗與各按鈕入口。
- `lib/gui/`
  GUI 行為與下載入口。
- `lib/gui/dialogs/`
  各管理視窗與部分上傳 helper。

### Headless jobs

- `lib/jobs/`
  不依賴 GUI 的工作單元，供 CLI 與 Actions 共用。

### Google Sheets

- `lib/google_sheets/`
  gspread / Google Credentials 取用。

### Runtime / workflow

- `.github/workflows/`
  GitHub Actions workflow 定義。
- `.github/runtime/`
  workflow 使用的靜態 runtime mapping 檔。

### 測試

- `tests/`
  以單元測試為主，覆蓋 constants、utils、API、部分 GUI helper、Google Sheets service。

## 設計原則

- GUI 與 headless job 共用相同的底層 API / data / upload 邏輯。
- runtime 設定檔與 repo 靜態資料檔分離。
- `union_id` 是聯盟與試算表路由的核心鍵。
- `account` 或 `BLABLALINK_DEFAULT_ACCOUNT` 是 headless 執行的核心身份來源。
- data 檔優先放在 repo / cloud data repo；敏感 runtime 檔透過 GitHub secrets 管理。
