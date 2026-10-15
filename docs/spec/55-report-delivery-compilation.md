# 已發布報告的本地發送候選

實作：application/report_delivery.py、adapters/local_outbox.py；只使用本地狀態庫與 mock provider。

## 固定來源與確認

- compile_report_candidate 只接受 succeeded 的 recompute_healthcheck，要求結果已發布，且 job 仍符合目前設定 head 與 desired target。
- 單一 sealed workbook 的 config／training／code 必須符合 job。報告需完整且無 warnings；空文字不產生發送，全部空報告回傳 None。
- 路由為獨立不可變 union_inputs 快照，routing_kind=report_routes；scope 與 reference 必須符合計算來源。每列含 member composite ID、target_thread、可選 mention_user_id。缺少需發送成員的路由或重複身份皆拒絕。
- mention identity 限非空 ASCII 數字、最多 32 字元，此為本地輸入邊界，並非完整 Discord 身份認證。
- ReportSendCandidate 固定 scope、intent、job、result、route snapshot 及所有 DeliverySpec，以 canonical JSON SHA-256 定義候選身份。
- prepare_batch 只保存候選，不入列。同 scope／intent 不得換內容，需新 intent 才能重新捕捉。
- confirm_batch 要求明確相同 candidate ID；可信的 ReportCandidateValidator 在同一寫入交易期間重新編譯來源並比對整份候選，拒絕篡改 target／文字與過期來源。
- 已接受批次的重播回傳原 outbox IDs，不重新驗證新 head，不重設已送狀態；這代表回放既有確認，並非確認新內容。

## 分段與交易

- formatter 接受完整產生的 cs code block，保留原文順序；每段自行開關 fence，優先換行切段，長單行按 Unicode code point 切開，不切斷 emoji。
- 每段上限 2000 UTF-16 units；標頭上限 512 units、內文上限 200000 units。使用者 mention 與原標頭只放首段，allowed_mentions 延用 DeliverySpec 的首段白名單。
- 0005_report_batches.sql 增加持久候選與批次 request 去重。成員 outbox、分段、候選 accepted 及 request 同交易提交，任一成員衝突會全批回滾。
- 沿用既有 outbox 的逐段序列、unknown 隔離與可信核對，不把整批入列成功當成整批發送成功。

## 驗收與限制

local_tests/test_report_delivery.py 驗證長單行／多行／emoji 無損、固定路由與 mention、候選篡改、路由缺漏／重複／異 scope／reference、標準更新使待確認候選過期、已確認重播、重啟與後段衝突全批回滾。

原編譯器／validator 只保證固定內容與計算來源仍有效，不單獨保證路由仍最新。[79 的本地受授權 outbox](79-local-routing-and-authorized-outbox.md) 另核對 route head／sender grant 與每段 start permit；原低階介面不得直接公開。真實 actor／principal 身分、遠端確認端點、Discord rate limit／receipt adapter 與雲端 outbox 仍未接線。
