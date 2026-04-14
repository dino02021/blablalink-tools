# BlaBlaLinkTool Spec

這份 spec 採拆檔維護。

原因：
- 專案同時包含桌面 GUI、Playwright 登入、自動化 API 擷取、Google Sheets 寫入、GitHub 雲端同步與 Actions workflow。
- 若全部塞進單一文件，之後每次調整 pipeline 或 runtime 檔案時會很難維護。
- 拆檔後可以讓 GUI、資料/設定、Headless jobs/Actions 分別演進。

## 文件索引

- [00-overview.md](./00-overview.md)
  專案範圍、核心能力、目錄分層、主要流程總覽。
- [10-runtime-and-data.md](./10-runtime-and-data.md)
  runtime 檔案、repo data、cloud data repo、GitHub secrets 與資料同步規則。
- [20-desktop-gui.md](./20-desktop-gui.md)
  桌面版 GUI 的登入、主畫面、按鈕功能與對話框責任。
- [30-headless-jobs-and-actions.md](./30-headless-jobs-and-actions.md)
  CLI、job layer、GitHub Actions workflow、union / nikke / sync 三條雲端流程。

## 閱讀順序

建議順序：

1. 先看 `00-overview.md` 了解整體架構。
2. 再看 `10-runtime-and-data.md` 了解哪些資料在本機、哪些在 repo、哪些在 GitHub secrets。
3. 如果主要維護 GUI，讀 `20-desktop-gui.md`。
4. 如果主要維護自動化、排程、雲端流程，讀 `30-headless-jobs-and-actions.md`。

## 文件原則

- 這份 spec 描述的是目前 repo 已實作的行為，不是理想狀態藍圖。
- 若程式與文件衝突，以目前 `main` 分支程式碼為準，並應同步修正文檔。
- 文件中盡量用模組名稱與資料流描述，不複製過多實作細節，以避免文件過快過時。
