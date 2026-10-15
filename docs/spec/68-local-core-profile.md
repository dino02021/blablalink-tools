# 本地核心量測與 Windows reader 修正

executors/local/profile.py 使用固定合成 30 成員、200 角色、每角色三技能，以及 30 人排刀問題；不讀帳號設定、不接 collector／Sheets／Discord，也不啟動 hosted runtime。

- `python -m executors.local.profile --output <new-report.json> --repeats 3`；重複次數 1..5，輸出不得覆寫，每 sample 有 15 秒／預留 1 秒、512 MiB 與合作式操作數上限。受限即保存失敗狀態、停止該 case 重複，不當成功。
- 記錄 wall／process CPU、每 checkpoint 取樣 RSS、檢查次數與固定結果 hash。p50／p95 採 nearest rank；只有三次 sample，p95 即該三次最大值，不能當正式容量估計。
- measured scope 包含核心計算、報告／結果 hash 與合作式檢查；合成 snapshot 建立在計時前，不含資料擷取、DB、staging／publisher、網路或平台 billing。

## 發現與修正

首次量測 [local-profile.json](../validation/2026-10-15-local-profile.json) 的健檢在約 6.26 秒／61358 次檢查觸及 512 MiB，未完成。Windows reader 每次呼叫重新定義 ctypes counter type 與 pointer signature，導致重複 metadata 配置持續增長。

修正為快取 Windows API handle／typed counter class，只為每次讀取建立 counter instance。新增 10000 次本機讀取的記憶體增量回歸，以及小型 profile 的結果穩定性／範圍測試。

同一輸入的[修正後紀錄](../validation/2026-10-15-local-profile-cached-memory.json)：

| 核心 | 三次 wall 範圍 | 最高取樣 RSS | 每次檢查數 | 結果 |
| --- | --- | --- | --- | --- |
| 健檢 | 1.47–1.83 秒 | 約 41.1 MiB | 198238 | 三次同 hash |
| 排刀 | 0.48–0.50 秒 | 約 40.0 MiB | 24260 | 三次同 hash |

環境為本機 Windows／Python 3.13.6；兩份原始 JSON 均保留。這是該合成案例與本機的測量，尚無大型 trait／歷史／指定刀矩陣、Linux hosted profile、硬性程序限制、p95 負載或免費帳戶實際用量驗收。
