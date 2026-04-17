# 桌面 GUI

## 入口

桌面模式的主要入口是：
- [lib/gui_main.py](../../lib/gui_main.py)

核心 app class 為 `LoginApp`。

## GUI 職責

GUI 只負責以下工作：
- 顯示帳號、狀態、按鈕與管理視窗
- 讓使用者選擇 account / sheet / mapping
- 啟動背景 thread 執行流程
- 將結果回寫到狀態列與 log

GUI 不應負責：
- 自己實作 API 路由
- 自己決定 sheet 匹配規則
- 自己維護與 headless 不一致的業務邏輯

## 啟動職責

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

## 登入流程

登入相關邏輯分散於：
- [lib/gui/login_flow.py](../../lib/gui/login_flow.py)
- [lib/auth.py](../../lib/auth.py)

規則：
- 優先使用既有 `sessions.json` 重建 session。
- 若 session 無法使用，再進入 Playwright 登入流程。
- 成功登入後要能回寫 `storage_state`。

## 主要使用者操作

### 登入後主畫面佈局

登入後主畫面採固定三段式：
- 第一排放登入前即可使用的管理按鈕：`管理雲端帳號`、`管理試算表`、`排程設定`、`選擇/管理帳號`
- 第一排最右側固定保留 `登出`
- 第二排放主要工作按鈕，順序固定為 `更新聯盟突襲傷害表`、`更新練度資料表`、`管理健檢表`、`上傳同步`
- 第三排只保留一個共用訊息欄位，所有登入後主流程按鈕都回寫到同一個狀態區
- 第二排按鈕列與第三排訊息列必須使用獨立容器；狀態文字長度不得改變第二排按鈕位置

按鈕狀態規則：
- 任一主流程按鈕開始執行後，主畫面其他按鈕一律禁用
- 包含登入、聯盟突襲更新、Nikke 更新、上傳同步
- 流程完成或失敗後，再由主畫面統一恢復按鈕狀態
- 不允許每個功能各自只禁用單一按鈕，避免重入與並行操作

### 聯盟突襲

按鈕對應流程：
- 下載聯盟突襲
  呼叫聯盟突襲下載流程。
- 上傳聯盟突襲
  呼叫聯盟突襲上傳流程。

### Nikke

按鈕對應流程：
- 下載單一玩家 Nikke 資料
  直接走 API 抓取。
- 批次下載 Nikke 資料
  直接對當前聯盟成員做批次下載，不再透過手動 member 清單視窗。
- 上傳 Nikke 資料
  直接依目前 account 的 `union_id` 路由到對應 `CharacterDetails` 試算表，不再先跳選擇視窗。
- 上傳同步等級
  直接依 `guild_id -> CharacterDetails` 試算表路由。
- 管理健檢表
  透過對話框管理 `nikke_healthcheck_members.json`，支援健檢妮姬名單的新增、刪除與篩選。

### 管理對話框

目前重要管理視窗：
- [lib/gui/dialogs/accounts.py](../../lib/gui/dialogs/accounts.py)
- [lib/gui/dialogs/sheets.py](../../lib/gui/dialogs/sheets.py)
- [lib/gui/dialogs/union_account_map.py](../../lib/gui/dialogs/union_account_map.py)
- `管理健檢表` 入口對話框與 `健檢妮姬名單` 對話框

規則：
- `sheet` 管理視窗新增試算表時，`union_id` 為必填欄位。
- GUI 編輯 mapping 時，不要在視窗內額外定義與 job layer 不一致的驗證規則。

#### 對話框特定行為

- **帳號對話框**：
  - `choose_or_manage_accounts()` 返回選取帳號或 None。
  - 雙擊列表項目等同套用選擇。
  - 新增/刪除帳號後更新列表並顯示綠/紅狀態訊息。

- **試算表對話框**：
  - 列表顯示 `type-sheet_name` 格式。
  - 支援新增與刪除試算表。
  - 子視窗（類型管理等）使用相同的常數型佈局規則。

- **試算表類型管理**：
  - 列表顯示試算表類型名稱。
  - 支援新增與刪除類型。
  - 子視窗遵循相同佈局規則。

- **聯盟突襲上傳對話框**：
  - 遵循標準對話框佈局模式。
  - 資料選擇、試算表選擇、月份選擇皆使用相同的 padding 與訊息顯示規則。

## 執行緒規則

所有會阻塞 UI 的工作都必須放到背景 thread：
- API 呼叫
- Playwright 登入
- 大量檔案整理
- Google Sheets 上傳

UI 更新規則：
- 背景 thread 不直接操作 Tk widget。
- 透過 `root.after()` 或等價方式回到主執行緒更新狀態。

## 共用邏輯規則

若某段流程同時被 GUI 與 headless 使用，應優先收斂到：
- `lib/jobs/`
- `lib/api.py`
- `lib/data_store.py`
- `lib/google_sheets/`

GUI 應只保留：
- helper UI
- 顯示文字
- 背景執行包裝

## 目前風險

目前 GUI 仍有幾個歷史負債：
- 某些 upload helper 仍從 GUI dialog 模組匯入共用函式。
- GUI 啟動時會同時帶入多個 watcher，改動時容易造成重複觸發。
- 若未來再新增一套只存在 GUI 的 routing 規則，會與 headless 行為分叉。

## GUI 修改開發規則

改 GUI 功能時，優先檢查：
- 是否已有對應 job 可直接重用
- 是否會改變 account、union_id、sheet routing 規則
- 是否會讓背景 thread 直接碰 UI
- 是否會讓 GUI 與 workflow 的實際行為不同步
