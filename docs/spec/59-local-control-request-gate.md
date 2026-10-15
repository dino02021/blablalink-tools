# 本地簽章控制請求

application/control_requests.py 與 adapters/local_request_gate.py 提供無 listener 的離線控制契約，0006 migration 保存 replay 證據。沒有建立 Worker、公開網址或正式金鑰設定。

- RequestPrincipal 明確給 principal ID、命令、scope、實際 workbook ID 白名單與記憶體中的 key ID→bytes。金鑰至少 32 bytes、不進 dataclass repr 或 SQLite；fixture 為合成固定字串。
- 僅接受 POST /v1/requests、schema_version=1、1..65536 bytes body。簽章涵蓋 principal／key／timestamp／nonce／request_id／method／path／body SHA-256，以 canonical JSON 的 HMAC-SHA256 驗證；timestamp 容許正負 300 秒。
- 簽章與 body digest 為 64 字元小寫 hex；constant-time compare。拒絕未知 key、過期、篡改、重複 JSON key、額外 body 欄位與無效 TaskSpec。
- body 只有 task；接受 recompute_healthcheck／generate_raid_draft 及只建候選的 reconcile，且仍需 principal 命令與 scope 授權。發布、確認、發送、憑證更新均不支援此入口。
- command→family 固定為 recompute_healthcheck→healthcheck、generate_raid_draft→raid；外部 caller 不能自創 family 逃過原 desired target 的 superseded 檢查。可信本地 coordinator API 保留一般 family 能力。
- reconcile 固定 family=config_reconcile，從同 scope 的 sealed reconcile_route 解析標準 resource，不能透過提示任意指定 source workbook；詳見 [薄 GS 橋接](65-thin-gs-reconcile-bridge.md)。
- 健檢從封存 union_inputs→healthcheck_route 解析真實 workbook，再比對白名單；training／config／inputs 的 kind、scope、reference 必須相符。排刀需該 scope 的 input_head 指向指定 problem，code 也須相符。不能靠 caller 另傳 workbooks 自稱有權限。
- nonce 依 principal／key 隔離，僅允許相同完整 envelope 重播；同 nonce 換 request 或簽章 metadata 拒絕。同 principal／request_id 只接受同 body hash，新 nonce 可重送相同 body。
- 每次重播仍驗時間、目前 key／policy／封存輸入授權，不能藉曾經受理繞過撤銷。不同 principal request ID 不互相碰撞。
- 新 envelope 入庫另受[持久容量與時間 watermark](67-local-control-capacity.md)限制；安全時間窗外 nonce 可清理，request 去重保留。
- replay 表先交易保存，submit_job 後才回覆 job；中間程序消失可重送，使用 principal／request_id 派生固定 coordinator request key，補完同一提交。gate receipt 本身不代表 job 已受理成功。
- principal 只是受驗證的服務身份；human actor 一律保存 unknown，沒有把簽章服務假裝成試算表編輯者。

local_tests/test_control_requests.py 的 21 項 mock 測試涵蓋完整重播／重啟、新 nonce 去重、過期／未來時間、method／path／schema、Unicode 無效簽章、未知／撤銷 key、nonce／request 衝突、body 篡改、重複 key／額外宣告／大小上限、實際 workbook 權限與 replay 保存後 crash 恢復。

尚未實作 hosted key registry／輪替、邊緣流量限制、歷史 request 歸檔、actor／確認權限、snapshot ingestion 授權或 D1 交易 adapter。HTTP parsing 與本地受理容量已有離線契約；正式接線仍須驗證來源物件入庫權限及該平台重放／撤銷行為。
