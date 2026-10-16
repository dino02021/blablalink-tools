# 115：Mock D1 共享存取與獨占維護鎖

## 範圍

新增 mock_d1_access。每次 persistent SDK 呼叫在啟動 Node 前取得共享 OS file lock，持有到 owned Job 停止與 finally 清理完成。一般呼叫可並行；離線維護取得同一 byte range 的獨占鎖，最多等 60 秒，不以固定 sleep 假設其他 SDK 已結束。

Windows 使用 [LockFileEx](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-lockfileex) 的共享／獨占模式與 FAIL_IMMEDIATELY，再由 monotonic deadline 控制等待。鎖定 EOF 外的 byte 不需初始化寫入；不同呼叫各開 handle，避免同 handle 的共享／獨占重疊特例。handle 不傳給 SDK 子程序。POSIX flock 分支尚無本機平台驗收。

## 契約

- 僅標記為 mock 且 schema digest 相符的工作目錄；拒絕 redirected lock file。
- 等待前與取得鎖後都核對 marker、storage 與 restore fence，避免先核對後等待的競爭。
- 同 thread 同工作目錄的巢狀呼叫直接拒絕，維護不得偷偷繞過共享鎖啟動 SDK。
- 非有限、bool、非正或超過 60 秒的 timeout 拒絕；timeout 與例外不得留下持有的鎖。
- 程序退出由 OS 釋放鎖。這是合作式本地保護，不能宣稱能阻止外部程式直接修改 storage；也沒有宣稱 writer fairness。

## 驗收

11 項新增測試及既有實際雙 SDK claim 測試共 12 項通過。涵蓋共享重疊、獨占雙向排除、真實 subprocess 被 kill 後釋放、等待期間新增 fence、錯誤 timeout 不建立 lock、例外清理，以及 SDK attach 時獨占維護不能進入。這不代表完整 D1 migration／backup 已完成；後續須在獨占區段實作有界快照與 fenced restore。
