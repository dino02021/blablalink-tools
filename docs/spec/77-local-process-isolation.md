# 本地計算子程序的時間／記憶體限制

executors/local/isolated.py 提供 compute-only mock worker：`python -m executors.local.isolated --workspace <marked-mock-directory> --code-sha <release>`。預設一次最多一個 job，整個 child invocation 250 秒、512 MiB、stdout／stderr 各 64 KiB；可明確設定有界 profile。它不提供 publish flag，不啟動 daemon／排程或外部服務。

## 啟動與 native 邊界

父程序先驗 mock marker、SQLite 與 restore fence，固定執行目前 Python／repo module，shell=False，環境僅保留 OS 暫存／系統與文字編碼所需值；不繼承 provider secrets、HOME、PYTHONPATH／startup injection。這是可信專案程式的資源限制，不是執行任意不可信程式的安全 sandbox，也沒有 OS 網路封鎖。

child bootstrap 先等待 start 握手，不匯入 worker。

- Windows：父程序建立私有 Job Object，設定 ProcessMemoryLimit（committed virtual memory）、最多一個 active process、KILL_ON_JOB_CLOSE，綁定 Popen 擁有的精確 process handle 後才送 start。不按 PID 重開 handle，也不允許 breakaway。建立／設定／綁定失敗直接拒絕，確保已產生的 child 停止。
- Linux：建立新 session，bootstrap 在匯入 worker 前套用 RLIMIT_AS 與 RLIMIT_FSIZE；逾時會 kill 仍在執行的 owned process group。此分支尚未在目前 Windows 主機實測，不宣稱跨平台驗收；它也沒有 Windows active-process=1 的等價限制。
- 其他平台拒絕啟動，沒有悄悄降級。

Windows memory limit 的語意見 [Microsoft Extended Limit Information](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_extended_limit_information)，process／close 規則見 [Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)。Linux address-space／file-size 定義見 [Python resource](https://docs.python.org/3/library/resource.html)。這些 native 上限和合作式 RSS 預算不是相同量測。

## 父程序監督與回報

stdout／stderr 指向臨時檔，父程序最多每 50 ms 核對 deadline、exit 與檔案大小，不用 communicate 無界收集。Windows 的輸出限制由輪詢觸發，可能在輪詢間短暫超出上限；讀回只取上限，暫存檔隨結束移除。時間上限也受 OS 排程與最多 50 ms 的監督粒度影響，不是 hosted deadline 的證明。

outcome 分 completed／deadline_terminated／output_limit_terminated／child_failed。child_failed 不猜測是否為 memory failure；native 可能拒絕 allocation 使 Python 以非零碼退出。stderr 僅回 bounded hash／truncated flag，不回內容；worker 包裝只接受 mock JSON receipt 且 network_providers 空，不暴露 raw stdout。

所有路徑都等待 child 停止並回收 handle。父程序被正常例外打斷也執行清理；Windows KILL_ON_JOB_CLOSE 支援父程序消失時終止 child。整批 memory／time profile 與 worker 逐 job 的合作式 budget 同時存在，先到的邊界生效。

## 不混淆 child 終止與 job 完成

逾時或 allocation failure 可能發生在 claim／checkpoint／SQLite 交易前後。父程序不將 job 改成 succeeded／failed、不強制釋放 lease、不自動重跑、不碰 publisher／outbox 的 unknown 狀態。停止證據僅指這個 owned child；後續仍由 coordinator 真實狀態／租約與既有 recovery 決定。

local_tests/test_process_guard.py 的 15 項案例在目前 Windows 實測非合作式無限迴圈、單次 256 MiB 配置被 96 MiB native 上限拒絕、有界輸出、真實 mock worker 只算不發布、環境過濾、marker／restore fence、profile 驗證、kill 不冒充 job 成功或解鎖、三種 receipt 破損與 native fence 失敗清理。

仍需：hosted deadline／memory adapter、Linux 真實驗收、跨程序效能矩陣、dispatcher 與此入口的明確採用。原 run_worker 入口仍可直接使用合作式模式；不能宣稱所有 executor 已自動具備硬性限制。
