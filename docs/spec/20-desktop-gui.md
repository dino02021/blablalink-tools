# Desktop GUI

## Entry

桌面模式的主要入口是：
- [lib/gui_main.py](../../lib/gui_main.py)

核心 app class 為 `LoginApp`。

## GUI Responsibilities

GUI 只負責以下工作：
- 顯示帳號、狀態、按鈕與管理視窗
- 讓使用者選擇 account / sheet / mapping
- 啟動背景 thread 執行流程
- 將結果回寫到狀態列與 log

GUI 不應負責：
- 自己實作 API 路由
- 自己決定 sheet 匹配規則
- 自己維護與 headless 不一致的業務邏輯

## Startup Responsibilities

`LoginApp.__init__()` 啟動時會做的事應視為固定基礎流程：
- 建立主畫面與狀態列
- 載入 accounts、sheets、union account map
- 初始化 log / temp / runtime 目錄
- 啟動 cloud data 同步相關 watcher
- 啟動 runtime 檔變動監看

修改啟動流程時注意：
- 不要在啟動階段做大量阻塞 API 呼叫
- 不要讓 watcher 重複啟動
- 不要引入會額外彈出命令列視窗的子程序

## Login Flow

登入相關邏輯分散於：
- [lib/gui/login_flow.py](../../lib/gui/login_flow.py)
- [lib/auth.py](../../lib/auth.py)

規則：
- 優先使用既有 `sessions.json` 重建 session。
- 若 session 無法使用，再進入 Playwright 登入流程。
- 成功登入後要能回寫 `storage_state`。

## Main User Actions

### Union Raid

按鈕對應流程：
- 下載 union raid
  呼叫 union raid download 流程。
- 上傳 union raid
  呼叫 union raid upload 流程。

### Nikke

按鈕對應流程：
- 下載單一玩家 Nikke 資料
  直接走 API 抓取。
- 批次下載 Nikke 資料
  直接對當前聯盟成員做批次下載，不再透過手動 member 清單視窗。
- 上傳 Nikke 資料
  直接依目前 account 的 `union_id` 路由到對應 `CharacterDetails` sheet，不再先跳選擇視窗。
- 上傳同步等級
  直接依 `guild_id -> CharacterDetails` sheet 路由。

### Management Dialogs

目前重要管理視窗：
- [lib/gui/dialogs/accounts.py](../../lib/gui/dialogs/accounts.py)
- [lib/gui/dialogs/sheets.py](../../lib/gui/dialogs/sheets.py)
- [lib/gui/dialogs/union_account_map.py](../../lib/gui/dialogs/union_account_map.py)

規則：
- `sheet` 管理視窗新增試算表時，`union_id` 為必填欄位。
- GUI 編輯 mapping 時，不要在視窗內額外定義與 job layer 不一致的驗證規則。

## Threading Rule

所有會阻塞 UI 的工作都必須放到背景 thread：
- API 呼叫
- Playwright 登入
- 大量檔案整理
- Google Sheets 上傳

UI 更新規則：
- 背景 thread 不直接操作 Tk widget。
- 透過 `root.after()` 或等價方式回到主執行緒更新狀態。

## Shared Logic Rule

若某段流程同時被 GUI 與 headless 使用，應優先收斂到：
- `lib/jobs/`
- `lib/api.py`
- `lib/data_store.py`
- `lib/google_sheets/`

GUI 應只保留：
- helper UI
- 顯示文字
- 背景執行包裝

## Current Risks

目前 GUI 仍有幾個歷史負債：
- 某些 upload helper 仍從 GUI dialog 模組匯入共用函式。
- GUI 啟動時會同時帶入多個 watcher，改動時容易造成重複觸發。
- 若未來再新增一套只存在 GUI 的 routing 規則，會與 headless 行為分叉。

## Development Rule For GUI Changes

改 GUI 功能時，優先檢查：
- 是否已有對應 job 可直接重用
- 是否會改變 account、union_id、sheet routing 規則
- 是否會讓背景 thread 直接碰 UI
- 是否會讓 GUI 與 workflow 的實際行為不同步
