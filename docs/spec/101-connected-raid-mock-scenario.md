# 101：人工來源到排刀發布的整合 mock 情境

## 單一連接情境

`executors/local/raid_scenario.py` 在全新 workspace 執行真實本地 adapters／coordinator／native child，串起先前分開驗證的功能。所有成員、工作簿、設定、身份與確認皆為 synthetic；無 formal provider、credential、Worker、D1 或外部訊息存取。

1. 以既有 demo 建立同源標準、養成與已成功發布健檢，保留尚未採用的新標準候選。
2. 建立同名且兩種排序的舊人工範圍，以明確 physical-row identity mapping 遷移；預覽後不相關人工備註更新仍保留。
3. 封存穩定 ID grid，再於 mock input 做尚未採用的另一個指定刀編輯。
4. 從原 raw grid 建候選，原子採用／排 draft；native child 真正計算並保存草稿，但不確認、不寫排刀生成區。
5. 使用 mock maintainer 明確接受 warning、確認固定 draft，再明確建立 publish intent；compute-only 檢查工作簿未變，另次 mock publisher 才寫五張 protected 排刀 tab。
6. 明確採用單 scope 共享狀態路由，第二個 native child 只計算封存狀態；mock publisher 寫三張 protected 狀態 tab，再跑一輪驗證沒有 self-feedback 工作。
7. 明確停止 mock writers 後清理兩份已終止 staging transport，核對所有工作簿完全不變。
8. 建立實際 backup／fenced restore，實際呼叫 worker 證明 restore fence 在 writer 初始化前生效；唯讀比對還原工作簿與 status。

兩個 native child 均使用既有 512 MiB／wall-time／owned-process guard，情境要求 outcome=completed 且 child 已停止。這是目前 Windows 本地證據，不推定 POSIX／hosted 已驗收。

## 正確描述完成範圍

結果使用 is_adopted_work_settled，而非把整套資料宣稱為瞬時同步。has_unadopted_human_edit=true，pending_member_grid_id 必須不同於已採用 member_grid_id；已封存來源的計算／確認／發布保持不變，最新人工草稿也保持不變。此情境不自動採用後續人工編輯。

scope 最近成功包含 healthcheck／raid_publication，另有獨立 owner 的共享 status publication；draft job 本身沒有 publication。輸出保留實際 source／draft／confirmation／job／commit IDs、native receipts、cleanup receipt 與 backup ID，便於對照。

## 入口與失敗行為

`python -m executors.local.raid_scenario --workspace <new-path> --output <new-json>` 只接受新 workspace；不 reset 既有資料，output 也只建立新檔。CLI 不啟動服務或排程。

情境中途失敗不偽造完成證據、不刪除已提交的歷史：draft child 失敗時沒有確認或排刀發布；status child 失敗時保留先前已確認發布的排刀，status 工作仍須後續處理。既有 coordinator 的恢復入口可繼續處理，不能靠重新跑情境覆蓋 workspace。

## 證據

`local_tests/test_connected_raid_scenario.py` 五項案例涵蓋整條資料連接、原生程序及 pending edit、實際 CLI evidence、拒絕既有 workspace、draft child failure 與 status child failure 不抹除先前成功。

另有實際獨立執行產生的 [2026-10-16-connected-raid-scenario.json](../validation/2026-10-16-connected-raid-scenario.json)。它固定 is_production_accepted=false，只是一次歷史 mock 執行的證據，不能取代 A01–A22 的缺口或遠端平台驗收。
