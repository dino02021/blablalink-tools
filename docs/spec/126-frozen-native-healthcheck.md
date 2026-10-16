# 126：固定快照與純計算 native child

## Bundle

FrozenHealthcheckBundle 只保存 canonical JSON 字串與 immutable identity metadata。固定三份 healthcheck_config／training／union_inputs，各自重建 Document 並核對原始 SHA-256、kind、canonical body、scope 與 reference；config 可 common 或同 scope。每份最多 16 MiB，整個 bundle 最多 48 MiB＋4 KiB，拒絕額外／重複／非 canonical／錯型別輸入。

Bundle 不包含 coordinator path、憑證、SQLite、D1 binding 或 provider callback。新 child 與 Root wrapper 共用 spec 125 的 artifact builder；scope／code／source IDs 都固定在 bundle identity，不另寫評分邏輯。

## Native profile 與檔案

Parent 自建 TemporaryDirectory，僅寫固定 mock marker 與 input.json；child 核對 marker／bundle hash，僅計算並新建 result.json。沿用一般 run_python_module 的 Windows native profile：512 MiB、一個 process、最多 250 秒、stdout／stderr 64 KiB、環境白名單。沒有為了 D1 SDK 偷放寬 child process 數。

Computed 結果以 canonical sidecar 保存，最多 16 MiB；stdout 只含固定 bundle／code／state／size／hash 小型 header。Parent 在 child 已停止後核對 header 精確 schema／identity、檔案型別／大小／SHA-256／canonical bytes，再核對輸出 route、source config／training／code、同一 standards 及 typed WorkbookView identity。

Header 的 schema_version／result_size 不接受 bool。未知／被改寫／錯 binding 的 header 或 sidecar 一律不採用；deadline、child failure 或無法證明停止時沒有可提升的 result_id。Pending 僅保留原本兩個 waiting_for_data reasons，不回零分、不寫 result sidecar。

FrozenHealthcheckExecution 保留 immutable serialized result／receipt，artifact 與 receipt properties 每次回 detached JSON；metadata repr 不展開大量 payload。結果 object ID 就是 canonical artifact 的 SHA-256，可直接對齊 D1 generic object transport。

## 驗收與後續

Bundle／artifact／native profile 共 35 項通過（8.60 秒）。包含 Root 結果與 identity parity、兩種缺資料、>64 KiB sidecar 與 <1 KiB stdout、無 coordinator 檔案、真實 deadline／停止狀態、input／result tamper、六種 forged headers、錯 duration 不啟 child，以及 detached immutable outputs。

另做實際 D1 parent transport 情境：三份 sealed documents 讀回重建 → 純 native child → 相同 artifact hash 上傳並 seal；不以其他 command 假裝 AUTO 工作，不建立或完成 compute job。結果待本批 focused／完整回歸記錄。

實際 D1 transport 情境已通過（31.15 秒）：完整 28-declaration versioned session 中最後保有四個 sealed object heads、零 compute jobs；計算與既有 Root 結果相同。正式完整工作流程不因此標記完成。

這只完成 spec 124 的 pure builder／frozen calculation／object transport 階段。D1 trusted freshness compiler、desired＋enqueue 原子採用、defer／resume、parent heartbeat／admission 與 publisher 仍未接線；不能把此運算情境當完整 worker。48 MiB input cap 也不是所有資料形狀都能在 512 MiB 內完成的容量承諾。

本批完整回歸：1,289 項通過（817.46 秒），22 項本地證據通過，is_production_accepted=false；AUTO／defer／lease pulse／publisher 仍未接線。
