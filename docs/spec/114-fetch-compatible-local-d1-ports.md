# 114：本地 D1 fetch-compatible 端口配置

## 實際問題與原因

本批完整 suite 曾出現三項 SDK 失敗（1,136 通過），皆為 Miniflare get_d1_binding 階段 fetch failed，該輪沒有更新驗收報告。短讀取、雙 client、連續寫入／封存／讀回與三個 isolated cases 都未重現。

保留全部 collection items 與前 186 項執行前序後，在 27 秒重現；bounded cause 是 `bad port`，workerd 尚存活，Job 峰值 203,948,032 bytes。已核對 pinned SDK：getD1Database 的 proxy 會等 ready，之後透過 Fetch 連到 runtime entry URL，故不以固定 sleep 解決。

本機唯讀 netsh 查詢顯示 TCP 動態端口 1024..15000，包含 [Fetch Standard §2.9 的 blocked ports](https://fetch.spec.whatwg.org/#port-blocking)。可 bind 的 OS 端口不必然可由 Fetch 使用；port:0 分配到禁用端口時，binding lookup 就會失敗。

## 修正

新增 createLoopbackRuntime。在任何 D1 binding、schema 或 task SQL 前，等待 SDK ready，檢查 HTTP、127.0.0.1、無 userinfo 與 fetch-compatible port。若端口禁用，先 dispose 該 runtime，再取新的 OS 端口，最多八次；其他 startup error 立即失敗。

僅重複配置本地端口，不重播 D1 操作、不改系統動態端口範圍、不解除 outbound deny、不提高 512 MiB／四 processes 限制。重配前的 runtime 子程序也必須停止，最後輸出會檢查所有歷史 SDK children。

## 證據

先加入實際 blocked-port case，原實作在 0.79 秒以同一 get_d1_binding／bad port 失敗；修正後通過。三項 focused tests 已通過：

- 強制先分配 blocked port，再取得安全端口，counter SQL 只寫一次。
- 純 port predicate／最多八次／非 port 錯誤不重試／非 loopback 拒絕。
- 八次耗盡時 schema 與 task SQL 都未執行，八個 SDK children 全部停止。

原 186 項前序回歸與完整 suite 均已通過，正式驗收維持 false。所有案例只用 localhost／mock D1，正式平台未接線。

## 預防

本地 SDK error 保留有限 cause chain、失敗 phase、child exit 與 owned Job 記憶體峰值；不再把所有 fetch failed 混成無法辨識的 envelope mismatch。依照 diagnosing-bugs workflow 建立實際 red→green seam，臨時 DEBUG probes 在確認後清理。

原前序回歸：保留所有 1,142 個 collection items，前 186 項通過（76.45 秒），停止點為原失敗 SDK 案例。臨時 DEBUG probes 已清理，保留 bounded error cause／child exit／resource counters 作為正式本地診斷；後續完整 suite 已通過 1,142 項，22 項本地證據通過，正式驗收維持 false。
