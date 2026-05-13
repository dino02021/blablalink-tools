# 程式風格與命名規範

## 命名與程式風格

### 檔案編碼
- 所有檔案一律 UTF-8 編碼；中文正常顯示。

### 布林值命名
- 所有布林變數與函式必須以 `is_` 開頭。

### 函式組織
- 對外函式置於檔案頂端。
- 對內函式必須以 `_` 開頭且置於檔案底端。
- **重要**：對內函式絕對不得巢狀在對外函式內。所有 `_` 開頭函式必須定義在模組層級。

### 程式流
- 盡量採用反向條件提前跳出，避免多層巢狀縮排：
  ```python
  # 好做法
  if not condition:
      return
  # 其餘邏輯
  
  # 要避免
  if condition:
      # 深度巢狀
  ```

## 對話框規則

### 對話框建立與關閉
- 所有對話框必須使用 `create_dialog(...)` 統一設定：
  - `grab_set()` 確保模態行為
  - `transient()` 設定視窗關係
  - 固定大小（不可調整）
- 關閉統一使用 `close_dialog()`。

### 禁止新增 `mainloop()`
- 子對話框必須使用 `grab_set()` + `wait_window()` 模式。
- 絕對不新增額外 mainloop 呼叫。

### 對話框行為
- **帳號對話框**：`choose_or_manage_accounts()` 返回選取帳號或 None；雙擊列表 = 套用；新增/刪除後更新列表並顯示綠/紅訊息。
- **試算表對話框**：列表顯示 `type-sheet_name`；可新增/刪除試算表；子視窗使用相同 padding 佈局。
- **管理試算表類型**：列表顯示 type；可新增/刪除；子視窗使用相同佈局。
- **聯盟突襲上傳**：遵循對話框佈局規則（所有資料/試算表/月份選擇皆使用相同 padding 與訊息顯示規則）。

## UI 常數與佈局

所有 UI 的 padding 與間距統一集中在 [lib/gui/ui_constants.py](../../lib/gui/ui_constants.py)。

### 佈局原則

1. **所有 UI 最外層一定使用 `create_frame` 包裹**
   - 確保一致的 padding 與 grid 佈局。

2. **Listbox 處理**：
   - 若對話框有 Listbox，外層 frame 一定要設定 `rowconfigure(0, weight=1)` 讓列表撐滿。
   - Listbox 每列文字左側加一個空白字元。
   - 若情境需要從列表選擇，雙擊 = 套用選擇。

3. **訊息提示**：
   - 最下方預設一個訊息 Label，初始為空字串。
   - 錯誤訊息顯示紅色、成功訊息顯示綠色。

4. **輸入框**：
   - Entry + entry_label 外層一定要用 `create_entry_frame()` 包裹。
   - 相連的多個 entry 必須共用同一個 `create_entry_frame()`，避免重複 padding。

5. **按鈕列**：
   - 按鈕外層一定要用 `create_btn_frame()` 作為容器。
   - 外框 padding：`BTN_FRAME_PAD`。
   - Grid padding：`BTN_GRID_PADX` / `BTN_GRID_PADY`。
   - 多顆按鈕之間用 `BTN_PADX_BETWEEN`，最後一顆用 `BTN_PADX_LAST`。

### 各元件類型的 Padding 常數

- **輸入框**：`ENTRY_LABEL_PADX` / `ENTRY_LABEL_PADY`、`ENTRY_PADX` / `ENTRY_PADY`
- **下拉選單**：`CB_PADX` / `CB_PADY`
- **列表**：`LIST_PADX` / `LIST_PADY`
- **訊息 Label**：`MSG_PADX` / `MSG_PADY`
- **按鈕框**：`BTN_FRAME_PAD`、`BTN_GRID_PADX` / `BTN_GRID_PADY`、`BTN_PADX_BETWEEN`、`BTN_PADX_LAST`

### UI 建立順序
- UI 元素必須按照**由左至右、由上至下**的順序建立。
- 這確保邏輯流暢與一致性。

### 佈局禁區
- **禁止使用 `columnspan`**，改用分別的 frame。

## 日誌

- 使用 `log_msg` / `log_exc` 進行日誌記錄，維持一致的深度，避免巢狀呼叫。

## 對話框回傳模式

對話框修改配置或需要回傳結果給父視窗時：
- 回傳字典，結構為：`{"ok": bool, "message": str, ...other_data}`
- 父視窗透過 `_set_status()` 方法顯示狀態。
- 這樣可確保父視窗顯示一致的反饋訊息。

## 測試

- 最少執行 `python -m py_compile` 於修改檔以確認語法正確。
- Commit 前確保語法驗證通過。
- 每次完成修改並驗證後，必須立即 commit 並 push 到目前分支；除非使用者明確要求暫停，不累積多次修改才推送。
- 修改 code 時必須同步檢查 spec；若行為、資料格式、GUI 操作或 job 流程有變，對應 spec 要在同一個 commit 內更新。

## 保留區域

- 常數名稱/值不動，除非另有指示。
- 不改登入/API 流程，除非明確需求。
- UI 常數定義不做改動，除非經過架構審查。
