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
- [58：本地蒐集分頁證據](58-offline-collection-evidence.md)
- [59：本地控制請求驗證](59-local-control-request-gate.md)
- [60：本地 workbook staging](60-local-workbook-staging.md)
- [61：GS 全員評分迴圈對照](61-gs-cohort-scoring-parity.md)
- [62：本地備份與還原](62-local-backup-restore.md)
- [63：本地物件保留與清理](63-local-object-retention.md)
- [64：A01–A22 本地證據與缺口](64-local-acceptance-evidence.md)
- [65：薄 GS reconcile 橋接](65-thin-gs-reconcile-bridge.md)
- [66：控制 HTTP 離線契約](66-offline-control-http.md)
- [67：控制請求容量與 nonce 保留](67-local-control-capacity.md)
- [68：本地核心 profile 與 reader 修正](68-local-core-profile.md)
- [69：同源健檢工作簿視圖](69-generated-workbook-views.md)
- [70：Mock 實體格線與保護](70-mock-physical-grid.md)
- [71：Mock 格線遷移與封存](71-mock-grid-migration.md)
- [72：十一欄標準草稿遷移](72-legacy-standard-migration.md)
- [73：排刀工作簿視圖](73-raid-workbook-view.md)
- [74：養成快照沿用與補抓規劃](74-training-reuse-planner.md)
- [75：本地 reference 候選與發布](75-local-reference-registry.md)
- [76：Reference 衍生養成投影](76-reference-projection.md)
- [77：本地計算子程序限制](77-local-process-isolation.md)
- [78：全聯盟唯讀狀態](78-read-only-system-status.md)
- [79：路由與授權 outbox](79-local-routing-and-authorized-outbox.md)
- [80：Collector 持久分頁與觀測](80-durable-collection-pages.md)
- [81：跨模組合成協作情境](81-integrated-mock-scenario.md)

- [82：名單生命週期與加入版本](82-roster-lifecycle-and-member-epochs.md)

- [83：本地名單輸入 head 與採用](83-local-union-input-heads.md)

- [84：排刀輸入編譯與來源過期](84-raid-input-compiler.md)

- [85：已確認排刀的本地發布](85-confirmed-raid-publication.md)

- [86：排刀來源原子採用](86-local-raid-source-adoption.md)

- [87：成員粒度補抓 executor](87-member-scoped-supplementary-collection.md)

- [88：worker 的 mock 收集 transport](88-mock-collector-worker-transport.md)

- [89：健檢持久後續調度](89-local-healthcheck-followup.md)

- [90：還原 fence 與唯讀初始化](90-restore-fence-before-initialization.md)

- [91：有界公平 workflow runner](91-bounded-workflow-tick-runner.md)

- [92：發布安全取消與原始基準](92-publication-no-write-abort.md)

- [93：Mock 工作簿跨程序互斥](93-mock-workbook-process-locks.md)
- [94：共享狀態表本地發布](94-shared-status-mock-publication.md)
- [95：已終止發布的暫存傳輸副本清理](95-terminal-staging-retention.md)
- [96：凍結 GS 模板表頭對照與差異](96-frozen-gs-header-contract.md)
- [97：排刀來源採用與草稿排程的原子動作](97-atomic-raid-source-draft-followup.md)
- [98：穩定 ID 人工排刀格線與封存來源](98-stable-member-raid-input-grid.md)
- [99：舊人工排刀格線的明確身分遷移](99-explicit-legacy-raid-grid-migration.md)
- [100：D1 原子 CAS 的離線契約](100-d1-atomic-cas-offline-contract.md)
- [101：人工來源到排刀發布的整合 mock 情境](101-connected-raid-mock-scenario.md)
- [102：本地 Miniflare D1 runtime 與有界程序樹](102-local-miniflare-d1-runtime.md)
- [103：以 receipt 驅動的低寫入 D1 CAS 候選](103-receipt-driven-d1-cas.md)
- [104：D1 物件分片傳輸基礎](104-d1-object-transport.md)
- [105：D1 分片清單有界分頁](105-d1-inventory-pagination.md)
- [106：本地 D1 大物件有界會話](106-bounded-d1-object-sessions.md)
- [107：D1 coordinator 後續整合契約（尚未實作）](107-d1-coordinator-integration-contract.md)
- [108：共用本地 D1 schema／session](108-shared-local-d1-session.md)
- [109：D1 frozen enqueue 與原子 compute claim](109-d1-frozen-compute-claims.md)
- [110：D1 heartbeat 與 sealed result 完成](110-d1-heartbeat-sealed-completion.md)
- [111：D1 checkpoint revision CAS](111-d1-checkpoint-revision-cas.md)
- [112：D1 本地 compute 有界回收](112-bounded-d1-compute-recovery.md)
- [113：多 SDK client 的持久路徑競爭](113-multiple-owned-d1-clients.md)
- [114：本地 D1 fetch-compatible 端口配置](114-fetch-compatible-local-d1-ports.md)
- [115：Mock D1 共享存取與獨占維護鎖](115-mock-d1-maintenance-lock.md)
- [116：Mock D1 實體備份與 fenced restore](116-mock-d1-physical-backup.md)
- [117：D1 多版本升級與 readiness 契約（待實作）](117-d1-versioned-readiness-contract.md)

## 撰寫規則

後續更新 spec 時，維持以下原則：
- 優先寫「現在程式怎麼運作」，不要寫理想化設計。
- 優先寫可驗證的規則，不寫空泛描述。
- 避免使用真實帳號、真實 union_id、真實 sheet_id。
- 不放維運教學、人工操作清單或外部平台設定步驟。
- 若 GUI 與 headless 共用同一邏輯，要明確標出共用入口。
- 只要 code 變更影響到流程、資料契約、routing、workflow 行為、GUI 操作方式或程式風格，必須在同一次修改中同步更新對應 spec。
- Code 與 spec 必須同時維護：功能實作不得先於 spec 長期存在；若發現既有功能與 spec 不一致，必須在同一次修改中補齊或修正 spec。
