# BlaBlaLinkTool 規格摘要

## 目錄與責任分工
- `main.py`：啟動入口，呼叫 `lib/gui_main.py` 的 `LoginApp`，只負責啟動與 log。
- `lib/gui_main.py`：主 GUI（登入、主要按鈕）。子對話框由 dialogs 模組提供。
- `lib/gui/layout.py`：共用視窗工具 `create_dialog` / `close_dialog` / `place_on_same_screen`（皆用 grab_set + wait_window）。
- `lib/gui/ui_constants.py`：統一 padding / gap 等 UI 常數。
- 對話框
  - `lib/gui/dialogs/accounts.py`：選擇/管理帳號
  - `lib/gui/dialogs/sheets.py`：管理試算表與類型
  - `lib/gui/dialogs/union_raid_upload.py`：上傳聯盟突襲資料（其子視窗沿用同樣佈局規則）
  - 下載聯盟突襲按鈕：`lib/gui/union_raid_download.py`
- Google Sheets：`lib/google_sheets/service.py`
- 其他業務/工具：`lib/api.py`、`lib/auth.py`、`lib/utils.py`、`lib/log.py` 等。

## 命名與程式風格
- 檔案一律 UTF-8；中文正常顯示。
- bool 以 `is_` 開頭；對外函式置頂，對內函式 `_` 開頭且置底。
- 對內函式不得巢狀在對外函式內，統一拉到檔案下方維護。
- if 盡量採用反向條件提前跳出，避免多層巢狀縮排。
- 對話框統一使用 `create_dialog(...)`（內部固定 grab_set + transient + 不可調整大小）；關閉用 `close_dialog`。
- Logging 用 `log_msg / log_exc`（避免巢狀過深）。

## UI 常數（lib/gui/ui_constants.py）
- 所有 padding / 間距集中在此檔；數值以 DEFAULT_PAD 為基準。
- 分類：FRAME/LIST、按鈕框與 Grid、按鈕間距、訊息區、標籤區。

## 套用原則
- 所有 UI 最外層一定使用 `create_frame` 包裹。
- 只要有 Listbox，外層 frame 就一定要設定 `rowconfigure(0, weight=1)` 讓列表撐滿。
- Listbox 每列文字左側加一個空白字元；若有「需從列表選擇」的情境，雙擊列表等同「套用選擇」。
- 最下方預設一個訊息 Label：預設空字串；錯誤紅色、成功綠色。
- entry 與 entry_label 外層一定使用 `create_entry_frame`。
- 相連的 entry 必須共用同一個 `create_entry_frame`，避免重複 padding。
- btn 外層一定使用 `create_btn_frame`。
- 輸入框/標籤：用 ENTRY_LABEL_PADX / ENTRY_LABEL_PADY、ENTRY_PADX / ENTRY_PADY。
- ComboBox：用 CB_PADX / CB_PADY。
- 列表：用 LIST_PADX / LIST_PADY。
- 訊息 Label：用 MSG_PADX / MSG_PADY。
- 按鈕列：外框用 BTN_FRAME_PAD，Grid 用 BTN_GRID_PADX/BTN_GRID_PADY；多顆按鈕用 BTN_PADX_BETWEEN，最後一顆用 BTN_PADX_LAST。
- UI 的建立必須按照「左到右、上到下」的順序。
- 禁止使用 `columnspan`。

## 對話框行為（現行）
- Accounts：`choose_or_manage_accounts` 返回選取帳號或 None；雙擊列表＝套用；新增/刪除更新列表並顯示綠/紅訊息。
- Sheets：列表顯示 `type-sheet_name`；可新增/刪除試算表；子視窗同樣使用常數佈局。
- Manage sheet types：列表顯示 type，可新增/刪除；子視窗同樣佈局。
- Union raid upload：遵循 dialogs 佈局（選擇資料/試算表/月份皆採相同 padding 與訊息顯示規則）。

## 禁區 / 保留
- 不新增 `mainloop`；子對話框統一 `grab_set() + wait_window()`。
- 常數名稱/值不動，除非另有指示。
- 不改登入/API 流程，除非明確需求。

## 測試
- 最少執行 `python -m py_compile` 於修改檔以確認語法正確。
