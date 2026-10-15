# 106：本地 D1 大物件有界會話（開發中）

## 目的

逐片重啟 SDK 可驗證 persistence，但不能有效完成 16 MiB 合法物件的邊界矩陣。新增固定 runner 的本地 object-session profile，在同一個 owned SDK 程序樹處理多個獨立小 batch；不代表 hosted Worker 將多個 control requests 合併成單一 invocation。

## 界限

- 單個 control frame 最大 64 KiB、每 batch 最多 50 statements；最多 2048 frames，fixture input 最大 24 MiB。
- 每個回覆 frame 最大 64 KiB，整體 reply 最大 32 MiB。SDK stdout 仍限制 64 KiB，只回報 reply 大小與 SHA-256。
- reply 位於父程序自建 TemporaryDirectory，固定檔名、新建寫入；父程序確認 SDK children 已停止後，再核對檔案型別、大小與 digest 並讀取，離開即清理。
- 沿用 Windows Job Object 最多四 processes、512 MiB aggregate、60 秒上限、環境白名單、start gate、outbound deny；一般 Python 與原 scalar SDK profile 不放寬。
- Python put_many 逐片驗證後建 frames，每片回讀核對；seal 與 read 在 Python 驗證完整原始物件。每個 frame 包含 schema guard。

## 驗收中

實際 16 MiB canonical JSON、分片部分上傳後重啟、完整上傳、seal 及再重啟讀回；另測錯誤 frame 在 SDK 啟動前拒絕。測試結果尚待完成，不先聲稱通過。

此處不代表正式 Worker CPU／記憶體／帳戶配額驗收，沒有任何正式平台或工作簿接線。認證、lease/fence、整體 quota／retention 與完整 coordinator 仍須補齊。

## 追加：SDK allocations 不能只看 wire 大小

首輪完整物件在單一大型會話上傳失敗，child exit=134，SDK stderr 明確回報 `Zone Allocation failed - process out of memory`。小型案例通過不代表整個 24 MiB fixture envelope 在 512 MiB SDK 記憶體內皆能成功。

Python port 現在每組最多 32 個 frames，依序開啟受監管的 SDK 會話；不提高既有 512 MiB aggregate budget。更大的會話 envelope 是拒絕／資源上限，不是可用容量承諾。失敗可留下部分 immutable chunks，後續依相同 manifest 重播與續傳，不將失敗視為 seal 成功。

完整 16 MiB 案例正在以分組會話重跑；仍待實際結果。

分組後實際完整 16 MiB canonical JSON 案例通過（151.68 秒）：部分上傳、重啟續傳、seal 完整驗證、再次重啟讀回。此時間是本機開發 SDK correctness 成本，不是 hosted latency 或可用配額承諾。另補 sidecar digest 竄改與已提交 frames 回覆遺失後重播案例；完整 suite 待回歸。

## 完整回歸的診斷追加

首輪完整 suite：1 項失敗、1,079 項通過，失敗為既有 same-fixture D1 比較器的 envelope 檢查；大型物件案例通過。該輪不輸出通過驗收報告。比較器獨立重跑取得正確 SDK versions、五個 operations、network deny 與 children closed，成功通過；原失敗的實際 envelope 未保存，因此不能斷言根因。

補強失敗診斷：emulator_failed 直接保留 bounded SDK error，其他 mismatch 回報固定白名單 metadata 與 operation count，仍嚴格拒絕、不自動重試掩蓋失敗。新增兩個注入診斷案例；再次完整回歸後才更新機器報告。

最新完整回歸：1,096 項通過，22 項本地證據通過，is_production_accepted=false。包含最大 inventory、16 MiB 合法物件、同 DB 共用 session 與原成本比較；先前失敗仍保留上述紀錄，不以重試自動掩蓋。完整 D1 業務 coordinator 尚未完成。
