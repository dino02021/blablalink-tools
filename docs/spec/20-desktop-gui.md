# Desktop GUI

## 主視窗

[lib/gui_main.py](../../lib/gui_main.py) 的 `LoginApp` 是桌面模式主入口。

主視窗分成兩個狀態：

- 未登入狀態
  顯示帳號、密碼、登入、帳號管理、試算表管理、雲端帳號管理
- 已登入狀態
  顯示聯盟突襲與 Nikke 相關操作按鈕

## 啟動初始化

`LoginApp.__init__()` 會做幾件事：

- 建立主視窗與 UI 狀態變數
- 讀取本機 `accounts`、`sheets`、`union_account_map`
- 確保 log / data / settings / temp 目錄存在
- 啟動 cloud data pull
- 啟動 cloud data watcher
- 啟動 secret file watcher

這表示 GUI 啟動本身就會帶有 runtime 同步責任，不只是畫面。

## 登入流程

登入由 [lib/gui/login_flow.py](../../lib/gui/login_flow.py) 進入，再落到 [lib/auth.py](../../lib/auth.py) 的 Playwright 流程。

### `auth.py` 責任

- 優先用既有 `sessions.json` 嘗試 headless 驗證登入
- 若舊 session 不可用，切回 headful Playwright 自動化登入
- 處理 cookie close、region 選取、填帳密、按登入
- 驗證成功後把新的 `storage_state` 存回 `sessions.json`

### 登入後資料

登入成功後 GUI 會保存：

- `_last_slug`
- `_last_role_name`
- `_last_role_id`

這些欄位會被之後的下載、上傳、同步功能重用。

## 主畫面按鈕與功能

### 聯盟突襲區

- `下載聯盟突襲資料`
  進入 [lib/gui/union_raid_download.py](../../lib/gui/union_raid_download.py)，背景執行 `run_union_raid_download`
- `上傳聯盟突襲資料`
  進入 [lib/gui/dialogs/union_raid_upload.py](../../lib/gui/dialogs/union_raid_upload.py)

### Nikke 區

- `下載本帳號妮姬資料`
  進入 [lib/gui/nikke_download.py](../../lib/gui/nikke_download.py)
- `批次下載妮姬資料`
  進入 [lib/gui/dialogs/nikke_batch_download.py](../../lib/gui/dialogs/nikke_batch_download.py)
  現行行為是直接對當前聯盟整批下載，不再開成員選取視窗
- `上傳妮姬資料`
  進入 [lib/gui/dialogs/nikke_upload.py](../../lib/gui/dialogs/nikke_upload.py)
  現行行為是直接整批上傳，不再跳檔案選擇視窗
- `上傳同步`
  直接呼叫 `run_sync_upload`

### 管理區

- `選擇/管理帳號`
  [lib/gui/dialogs/accounts.py](../../lib/gui/dialogs/accounts.py)
- `管理試算表`
  [lib/gui/dialogs/sheets.py](../../lib/gui/dialogs/sheets.py)
- `管理雲端帳號`
  [lib/gui/dialogs/union_account_map.py](../../lib/gui/dialogs/union_account_map.py)

## GUI 與 job layer 的邊界

GUI 現在主要做三件事：

- 收集使用者操作意圖
- 更新按鈕 disable/enable 與狀態字串
- 在背景 thread 內呼叫 `lib/jobs/*`

真正的資料下載、sheet routing、Google Sheets 寫入邏輯已經大多下沉到 `lib/jobs/`，這讓 GUI 和 Actions 可以共用同一套核心流程。

## GUI 特有邏輯

雖然大部分業務已下沉到 job layer，仍有兩塊重要邏輯還在 GUI 模組：

- `union_raid_upload` 的部分上傳實作與 sheet 寫入 helper
- `nikke_upload` 內的 worksheet 掃描、欄位對齊、變化表更新、WebApp 通知 helper

其中 [lib/jobs/nikke_upload.py](../../lib/jobs/nikke_upload.py) 會直接 import `lib/gui/dialogs/nikke_upload.py` 內的 helper。  
這是一個實作上存在的跨層依賴，維護時要特別注意。

## UI 狀態管理

主畫面用多個 `StringVar` 顯示不同區塊的狀態：

- `status_var`
  一般登入 / 主流程狀態
- `union_status_var`
  聯盟突襲狀態
- `nikke_status_var`
  Nikke 下載 / 上傳 / 同步狀態

長時間動作一律走 background thread，完成後透過 `root.after()` 回主執行緒更新 UI。

## GUI 限制

- GUI 依賴本機桌面與 Playwright browser；不適合直接在 server 環境跑。
- `auth.py` 的 headful fallback 會開啟可見 browser，必要時還會保留 30 分鐘等待人工介入。
- 上傳 helper 與 GUI 模組耦合仍偏高，後續若要更清楚分層，可再抽到獨立 service / upload helper module。
