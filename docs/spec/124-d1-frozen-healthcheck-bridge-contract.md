# 124：D1 與 Python frozen 健檢計算橋接（待實作）

## 已核對的現況

run_healthcheck_job 經 _run_healthcheck_job 讀取固定 healthcheck_config／training／union_inputs，核對 scope、reference、member epochs、route，呼叫 calculate_healthcheck 與 render_healthcheck_view；輸出 standards 就是計算使用的 config.payload。

目前 isolated worker 由 child 開啟 Root mock SQLite workspace。D1 SDK 則需要獨立多 process profile；一般 Python child 仍只允許一個 process。因此不能讓現有 child 偷啟 SDK，或另建臨時 SQLite 假裝 D1 的 authoritative coordinator。

Graph 採 Verify，generation 2026-10-16T10:38:50Z；run_healthcheck_job outbound 五個可見 callees、run_python_module inbound 一個非 test caller，無截斷。相關 paths coverage 回報 metadata_changed，已另讀當前 source；沒有把 graph coverage 當作完整性保證。

## 下一階段範圍

1. 抽出共用、無 store 副作用的健檢 artifact builder，保留現有 scope／epoch／缺資料與 route 規則。Root wrapper 與新 frozen child 共用它，不能另寫一套評分或門檻投影；原有結果 identity／scores／view／standards 必須相同。
2. Parent 只從 D1 讀取已 sealed 且 SHA-256 完整驗證的固定 documents，準備 owned input bundle；child 只計算，不開 D1、Root SQLite、Sheets、provider、憑證或網路連線。
3. Child 沿用一般 native Python profile 的 512 MiB／一個 process／wall／stdout 上限；大型結果寫 owned bounded sidecar，stdout 僅小型 identity／size／hash。Parent 在 child 停止後驗證 bytes／canonical JSON／bundle／code identity，才可上傳並 seal result。
4. 下載／上傳大量 chunks 與等待 child 時，parent 必須有 bounded heartbeat／lease／epoch checks。現有 execute_frames 每組 32 frames 尚沒有 D1 lease pulse 接線；不能以單次 claim 的 TTL 假設整段 16 MiB 作業都完成。
5. AUTO healthcheck 尚不能直接加入 FROZEN_COMMANDS：須由可信 domain compiler 產生 config／training／union_inputs／membership／route pins，以及 desired plan 的 head revision。Desired plan 先固定其內容 hash，再納入 TaskSpec，避免 fingerprint 與 desired head 的循環依賴。
6. Compiler 必須保留 request replay／source-head ABA／code affinity／scope fallback 與 global publisher barrier；不能讓外部 caller 自行宣告 pins 繞過 freshness。採用 desired 與 enqueue 必須在同 D1 atomic batch，不能以 Python 先寫 head 再排隊冒充原子。

## 追加遺漏

- 既有 D1 compute_jobs enum 尚無 waiting_for_data／capacity_blocked／auth_required。缺 training epoch 或 weighted metrics 時不能改判成功／零分，也不能一律改成 failed 或立即 retry 形成循環；需要明確持久 defer／resume 契約，或具驗收的 job schema migration，才能接上完整 worker。
- 完整 composed schema＋readiness guard＋32 KiB binary chunk 需實際 SDK 封包邊界驗證；現有 object-only 16 MiB 與較小 versioned fixture 證據不能直接擴稱所有組合都可用。
- Input bundle 的總上限不是 512 MiB 內一定可解析／計算的容量承諾。最大資料需獨立 memory／deadline 矩陣；超限不 seal／complete，也不保留有效完成授權。
- 先完成 pure builder／frozen child 與離線 parity，再實作 D1 defer、可信 compiler／atomic desired enqueue、parent heartbeat 與最後的 D1 計算情境。正式 publisher／Sheets、hosted dispatch、帳戶 admission 與大型分層 schema 另行驗收。

所有 fixtures 都是新建 mock documents，不讀正式試算表。此文件是下一段實作契約，不代表橋接已完成。

## 先期封包量測

2026-10-16 純 frame 量測：object＋head＋compute＋checkpoint＋recovery 的 24 declarations，加 migration ledger 後共 28 declarations，帶 revision=0／epoch=1 readiness 及一個 32,768-byte D1Binary echo，使用 SDK helper 相同 JSON serializer 的完整 payload 為 57,201 bytes（上限 65,536）。這只證明此固定形狀的序列化界限；仍須實際 SDK round-trip，不擴稱任意未來 schema 或最大物件都已驗收。
