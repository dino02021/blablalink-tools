# 113：同 persist directory 的多 SDK client

## 目標與方法

既有 parallel_batches 在同一 binding 競爭；本批另外以兩個 Python threads 各開 owned SDK instance，指向同一 marked persist directory，使用不同 claim request 競爭一個工作。

強化 start gate：兩個 Node roots 都先附到自己的 Windows Job Object，於 attach barrier 確認同時存活，才開放各自 one-use stdin gate。每個 SDK tree 仍最多四 processes／512 MiB；不改動正式環境。

若某 client 回覆 contended／unproven，待兩個初次呼叫結束後，只以原 request 做一次補查／重播；不能換 request 意外再領工作。最後必須只有一個 lease、generation=1、attempt=1、claim receipt=1，且原 sealed TaskSpec 可讀回。

加入 root overlap barrier 與 TaskSpec 完整性後的實際 SDK 案例已通過（18.19 秒）。此案例不聲稱所有呼叫都無 contention，也不以 retry 掩蓋交易結果；成功標準是固定 request 的最終證據與沒有重複授權。

持久 mock D1 的一致性 backup／fenced restore 尚未接現有 Root workspace 備份，需要後續專用驗證；不將 state.sqlite 的既有備份證據擴稱包含 SDK storage。

最新完整回歸：1,142 項通過，22 項本地證據通過，is_production_accepted=false；包含本模組、完整 D1 組合與 SDK 端口回歸。先前完整 suite 的 SDK 失敗原因與修正見 [114](114-fetch-compatible-local-d1-ports.md)。
