# BlaBlaLinkTool 開發規格

## 目的

這組文件的目標是描述目前專案的實作結構、資料契約、流程邊界與修改時要遵守的規則。

它不是：
- 使用者說明書
- 部署教學
- 雲端維運手冊

## 閱讀順序

1. [00-overview.md](./00-overview.md)
   先看專案目的、主要模組、共用原則。
2. [10-runtime-and-data.md](./10-runtime-and-data.md)
   再看所有重要資料檔、資料格式、路由規則與來源優先序。
3. [20-desktop-gui.md](./20-desktop-gui.md)
   需要改 GUI 時看這份。
4. [30-headless-jobs-and-actions.md](./30-headless-jobs-and-actions.md)
   需要改 CLI、job layer 或 workflow 時看這份。
5. [05-coding-style.md](./05-coding-style.md)
   程式風格、命名規範與 UI 佈局規則。
6. [40-local-migration-core.md](./40-local-migration-core.md)
   新架構的本地計算核心，尚未接入正式流程。
7. [41-local-snapshots-and-reports.md](./41-local-snapshots-and-reports.md)
   不可變快照、人工修正、報告及本地完整性閘門。
8. [42-local-state-coordination.md](./42-local-state-coordination.md)
   本地持久任務、設定發布、執行與發布權限、預算及競爭測試。
9. [43-mock-sheet-publication.md](./43-mock-sheet-publication.md)
   mock 工作簿生成區、封存結果發布與部分成功恢復。
10. [44-local-snapshot-chunks.md](./44-local-snapshot-chunks.md)
    大型快照分片、續傳、完整性與封存閘門。
11. [45-local-layout-planning.md](./45-local-layout-planning.md)
    穩定欄位、確認計畫、封存與型別化儲存格請求。
12. [46-local-training-normalization.md](./46-local-training-normalization.md)
    原始欄位白名單、來源完整性、持有判斷與全部詞條投影。
13. [47-local-report-outbox.md](./47-local-report-outbox.md)
    固定發送意圖、分段狀態、未知結果與 mock Discord。
14. [48-local-raid-inputs-and-simulation.md](./48-local-raid-inputs-and-simulation.md)
    排刀輸入、指定／歷史刀與精確階段扣血模擬。
15. [49-local-raid-round-selection.md](./49-local-raid-round-selection.md)
    不可變排刀輸入、候選來源池、前三輪與剩餘刀補洞。
16. [50-local-raid-finalization.md](./50-local-raid-finalization.md)
    改派、指定保留、元素交換、最終補刀與 GS 全流程對照。
17. [51-local-raid-confirmation-and-view.md](./51-local-raid-confirmation-and-view.md)
    不可變排刀草稿、輸入與確認 CAS、過期提示及成員 ID 檢視。
18. [52-local-compute-budget-and-resume.md](./52-local-compute-budget-and-resume.md)
    合作式執行預算、heartbeat、排刀階段 checkpoint 與本地續跑。
19. [53-offline-sheet-workflow-and-cli.md](./53-offline-sheet-workflow-and-cli.md)
    同源標準、候選 reconcile、健檢回寫與完整離線 demo CLI。
20. [54-legacy-standard-grid-and-role-types.md](./54-legacy-standard-grid-and-role-types.md)
    舊版六列／十二欄標準解析、字典身份與同角色多類型模型。

## 涵蓋範圍

文件只描述目前 repo 內有效的行為與規則：
- 桌面 GUI
- Playwright 登入與 session 使用
- BlaBlaLink / Nikke API 擷取
- Google Sheets 上傳
- headless jobs
- GitHub Actions workflow 對 job layer 的包裝方式
- 程式碼風格與命名規則

- [55：已發布報告的本地發送候選](55-report-delivery-compilation.md)
- [56：有界本地 worker](56-offline-worker.md)
- [57：本地計算資源檢查](57-local-compute-resources.md)

## 撰寫規則

後續更新 spec 時，維持以下原則：
- 優先寫「現在程式怎麼運作」，不要寫理想化設計。
- 優先寫可驗證的規則，不寫空泛描述。
- 避免使用真實帳號、真實 union_id、真實 sheet_id。
- 不放維運教學、人工操作清單或外部平台設定步驟。
- 若 GUI 與 headless 共用同一邏輯，要明確標出共用入口。
- 只要 code 變更影響到流程、資料契約、routing、workflow 行為、GUI 操作方式或程式風格，必須在同一次修改中同步更新對應 spec。
- Code 與 spec 必須同時維護：功能實作不得先於 spec 長期存在；若發現既有功能與 spec 不一致，必須在同一次修改中補齊或修正 spec。
