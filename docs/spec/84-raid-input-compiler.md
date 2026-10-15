# 排刀輸入編譯與來源過期防護

application/raid_inputs.py 將目前已發布健檢、固定 union_inputs、raid_settings 與 raid_member_inputs 編譯為 RaidProblem。全程讀取本地 immutable objects，不讀中間試算表，不抓正式資料，也不自動確認或發布。

## 固定來源

健檢 job 必須為 recompute_healthcheck、succeeded、有 succeeded publication receipt、仍符合 desired／config／union-input head，且 code 與此次編譯相同。receipt 代表本地 coordinator 記錄，不代表目前遠端格線已回讀。

編譯器取原 config／training／名單／override，以同一核心重算，核對 sealed workbook 的 config、training、code、calculation ID 與完整 scores。加入 epoch 必須由 training 證明。僅從這份分數取每個 composite MemberKey 的五屬性排名；未有計分角色的屬性為 null，不自行補造名次。

raid_settings 為 union_inputs subtype，raid_schema_version=1，固定三階 HP、五 boss 屬性、base_sync、五項 base_damage_yi、element_tie_order 與 overflow_policy。raid_member_inputs 同版本，明列每位成員的 sync、三個 slots、五項 damage_overrides_yi 與 history。兩份文件必須與名單同 scope／reference，各最多 8 MiB。

member inputs 最多 300 人，必須完整覆蓋目前名單，拒絕重複／外部／缺少身分及未知欄位；名稱來自目前名單，不能拿同名猜身分。history 最多 900 刀、每人最多三刀，唯一 knife ID、kind 必須 history 且成員仍在名單；其 stage／boss／damage 由既有 typed model 驗證。指定刀原字串與空白、明確 override 交由既有模型處理，編譯器不排序或回寫人工欄。

輸出固定 raid_compiler=raid-input-v1、healthcheck_job／result／config／training、union_inputs、兩份 raid 輸入及 code。problem ID 綁定完整內容。

## 採用與工作

observe_problem 在原子交易內檢查來源目前性；有 compiler 標記時重新編譯並比對完整問題，不能假借合法來源 ID 修改 rank、sync 或指定刀。managed scope 不接受略過編譯器的 legacy problem。

raid_task_spec 固定 problem 與全部來源版本。coordinator 在新 request 入列、claim／dispatch、完成與發布前核對編譯依賴；來源健檢變更後，舊 queued／running／computed 排刀不能成為目前結果。run_raid_job 亦拒絕未綁定依賴的 compiled task。unmanaged legacy mock 案例保留原離線契約，不宣稱已經過此編譯。

## 確認與狀態

新確認除了 problem head／revision，還檢查來源健檢、config／desired 與 managed 名單 head。任一來源失效，即使尚未 observe 新 problem，也拒絕新確認。

既有確認仍保留原 view；精確 request／intent 重播返回原 receipt，不改 head 或洗掉 stale。confirmation_status／confirmation_workbook 及唯讀系統狀態立即標示過期。唯讀 adapter 共用相同 current 檢查，直接沿用既有 query-only snapshot，不執行 LocalStateStore 初始化或 migration；壞 problem 只隔離所屬 scope。

## 本地驗證與尚缺

local_tests/test_raid_inputs.py 覆蓋真實 sealed 健檢排名、錯 scope／reference、缺人／重複／外部 ID、未知欄位、history、未發布／不同 code、問題偽造、名單與健檢 desired 變更立即 stale、queued／running／computed fence、實際 draft 工作不自動確認及壞物件隔離。

尚未實作 confirmed plan 的實體 publisher eligibility／路由與遠端發布、raid 設定與人工輸入的獨立採用 head、GS 人工範圍擷取或正式來源。新 immutable 文件本身不代表使用者已採用，只有 observe 的 problem 固定該設定。正式語意與免費平台協作仍須依主規格後續完成。

85 補上 confirmed plan 的獨立 mock 發布候選、publisher eligibility 與五 tab staging 寫入；正式 adapter／公開路由仍未接線。
