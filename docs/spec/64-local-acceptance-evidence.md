# A01–A22 本地證據與缺口

機器可讀映射：local_tests/acceptance_matrix.json。最近證據：[2026-10-16-local-acceptance.json](../validation/2026-10-16-local-acceptance.json)，1,142 項本地 mock 測試通過，22 項都有指定本地案例通過；is_production_accepted 固定 false。這不代表 22 項完整需求已驗收。跨模組合成證據另見 [81](81-integrated-mock-scenario.md) 與 [101](101-connected-raid-mock-scenario.md)，後者區分已採用工作完成與未採用人工草稿。

| 需求 | 本地證據範圍 | 尚未完成範圍 |
| --- | --- | --- |
| A01 | 候選後編輯仍固定原版本 | GS 事件／編輯範圍 |
| A02 | 同源設定重算、不重抓、八個 typed workbook 格線、清尾與人工 tab 保護、凍結 GS 十二欄標準表群組／merge 對照及練度表頭差異分類 | 遠端 grid／protection 與完整舊 header 相容 |
| A03 | 全員分數／總分／排名與 GS 50 組對照 | header／nickname／cache |
| A04 | 門檻、浮點與排名邊界 | 正式自訂儲存格型別 |
| A05 | 缺 detail 不當零、function ID 投影、coverage／成員補抓 executor、reference CAS 與相容衍生投影 | hosted／細粒度／真實完整性 |
| A06 | composite identity、名單轉換／封存、epoch、head CAS／舊工作 fence 與補抓後計算 | 全域 inactive 與舊人工區搬移 |
| A07 | request／dispatch 持久去重、D1 七步與 receipt-driven CAS、16 MiB 分片、分頁／sealed object、共用 schema session、並行／response-loss／positive lookup | 完整 D1 任務 adapter／遠端主體與平台 callback |
| A08 | 兩連線 publisher 競爭與舊 claim 拒絕 | 跨 runtime 競爭 |
| A09 | shared resource 鎖、route head／sender CAS 與 claim 競爭互斥 | 真實路由／principal／遠端 CAS |
| A10 | 新 desired 使舊計算 superseded | 舊遠端 invocation 停止證據 |
| A11 | 薄 GS 簽章提示到 Python 候選、內容去重 | HTTP 接線與正式 trigger／排程 |
| A12 | timeout 隔離、回讀／重啟及 phase／全基準安全取消 | 真實 Sheets HTTP 核對 |
| A13 | 單一 fanout／壞資料隔離、唯讀跨聯盟一致 snapshot、共享狀態三 tab mock publisher／內容去重／次序與有界 worker hook | 真實共享表 adapter／歷史分頁／遠端 scope 授權 |
| A14 | auth 停止且不 fallback cache、durable pages／觀測、穩定 revision 的 fresh head 契約 | 真實 Session／provider 完整性／revision 對應 |
| A15 | GS 固定排刀案例 | tie／guard 差異確認 |
| A16 | 排序 view／五 tab 視圖不改指定刀、sealed 來源編譯／依賴即時 stale、確認保留原內容、confirmed mock publisher／五 tab 寫入、原子來源採用與 batch-bound 草稿排程、穩定 ID 人工格線擷取／raw lineage、已知舊格線的明確 row mapping mock 遷移 | 真實 adapter／公開路由／正式舊身分對照與 Google 擷取、健檢更新後重編譯調度 |
| A17 | unknown 不盲重發、逐段路由／grant／一次性 start permit、跨 intent 防重 | 真實 principal／Discord adapter／遠端確認與正式採用 |
| A18 | 預算原子保留、unknown 不退款、D1 guard／receipt-driven 讀寫成本實測、零寫入 replay／positive lookup、unknown meta 不當零 | 共用帳戶實際用量／降頻、D1 admission／完整成本 |
| A19 | code affinity、local／hosted 邊界 | 同遠端備援協定／release distribution |
| A20 | budget／checkpoint／恢復，Windows child native memory／wall time 與 kill 狀態邊界、獨立 SDK Job 的 aggregate memory／process tree deadline | hosted profile、Linux 實測與 dispatcher 採用 |
| A21 | 缺片／取消不改結果、schema CAS、mock 格線遷移／archive、十一欄草稿遷移、已終止 staging／共享 chunks 安全清理與 receipt | 真實 staging／merge／protection、獨立 chunks／archive／history 清理 |
| A22 | 備份還原 fence、回滾另取 publisher | binary／降版與跨 runtime ownership |

## 報告生成

先以 `python -m pytest local_tests --junitxml <temporary.xml>` 產生真實本地測試紀錄，再以 `python -m executors.local.acceptance --junit <temporary.xml> --output <new-report.json>` 生成報告。輸出只可新檔；不啟動測試、不連外、不操作試算表。

application/acceptance.py 會將參數化案例映射回明確 pytest node ID，缺案例／skip 不視為通過，任何參數失敗都使對應需求失敗，suite_status 也保留未映射測試的失敗。每項必須保留 remaining，避免測試數量被誤用成正式驗收。

證據 node 可包含 lower-case 數字的 module／function 名稱，仍只接受完整 local_tests/test_*.py::test_* base node；參數案例 suffix 不直接寫進 matrix，而以既有聚合保留全部結果。路徑越界或部分匹配不放寬。

## 後續順序與外部依賴

仍可獨立開發：完整舊 header 對照、全域 inactive／舊人工資料與名稱格線遷移、健檢更新後 raid 重編譯調度／人工格線 semantic reconcile、自動 rollback／POSIX 實測、補抓 hosted 契約、薄 GS 與 hosted entry 的平台離線契約、D1 adapter 契約、獨立 chunks／archive／history／receipt 清理及更完整效能矩陣。不能因已有 22 項本地證據就 block 或標完成。

真正需要後續外部決策或驗證：正式工作簿與可編輯範圍盤點、正式服務 principal／key、實際 provider 的完整性欄位、帳戶共享免費用量、GS tie／安全 guard 差異採用、原 writer 停用證據、正式切換與 rollback ownership。這些不在目前授權的本地 mock 執行範圍內。
