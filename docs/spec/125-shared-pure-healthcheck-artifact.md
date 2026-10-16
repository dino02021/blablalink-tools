# 125：共用純健檢 artifact builder

新增 application.healthcheck_artifact.build_healthcheck_artifact，只接受 immutable config／training／union_inputs、scope 與 code identity，不持有 coordinator／Sheet／provider。既有 Root run_healthcheck_job 保留 command／lease／budget／completion 邊界，改為呼叫同一 builder。

Roster、display names、overrides、membership epochs、reference、固定 route 的核對與 calculate_healthcheck／render_healthcheck_view 順序沿用原邏輯。輸出的 standards 仍是評分當下的 config.payload，config_id／snapshot_id／scores／reports／view／view_id 與舊結果一致；沒有重寫數值規則或排名。

純函式以 HealthcheckInputsPending 回報 training_membership_epoch_unproven／training_incomplete；Root wrapper 仍轉為 waiting_for_data，不完成或發布不完整資料。ComputeLimit 仍由原有 budget context 處理。

四項新 parity／pending／scope tests 通過。首輪較大 focused 集合為 73 通過、1 fixture 失敗：epoch 誤填整數而非 64 字元身分 hash，沒有降低 production validator；修正 fixture 後四項新案例通過，相關既有流程將再回歸。

另外 spec 124 的完整 28 declarations＋readiness＋32 KiB binary echo 已以實際 SDK 通過，延續 57,201-byte 純 frame 量測；此證據不等同最大 16 MiB composed object 或 D1 完整健康工作已驗收。

Frozen child／sidecar／parent lease pulse 與 D1 AUTO／defer 接線仍依 spec 124 後續完成，所有驗證只用 mock documents。

修正 fixture 後，包含既有 sheet／view／workflow／worker／member epochs 與完整 D1 frame 的 74 項 focused 全部通過（34.67 秒）。Frozen child／sidecar 後續實作見 spec 126；其餘 parent pulse／AUTO／defer 仍待接線。

本批完整回歸：1,289 項通過（817.46 秒），22 項本地證據通過，is_production_accepted=false；AUTO／defer／lease pulse／publisher 仍未接線。
