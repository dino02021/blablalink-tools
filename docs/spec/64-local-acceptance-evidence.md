# A01–A22 本地證據與缺口

機器可讀映射：local_tests/acceptance_matrix.json。最近證據：[2026-10-15-local-acceptance.json](../validation/2026-10-15-local-acceptance.json)，423 項本地 mock 測試通過，22 項都有指定本地案例通過；is_production_accepted 固定 false。這不代表 22 項完整需求已驗收。

| 需求 | 本地證據範圍 | 尚未完成範圍 |
| --- | --- | --- |
| A01 | 候選後編輯仍固定原版本 | GS 事件／編輯範圍 |
| A02 | 同源設定重算、不重抓、八個 typed workbook 視圖 | mock 格線實體化／保護與舊 header 相容 |
| A03 | 全員分數／總分／排名與 GS 50 組對照 | header／nickname／cache |
| A04 | 門檻、浮點與排名邊界 | 正式自訂儲存格型別 |
| A05 | 缺 detail 不當零、function ID 投影 | reference 發布與補抓規劃 |
| A06 | composite identity 與人工欄位置保護 | roster 生命週期與搬移 |
| A07 | request／dispatch 持久去重 | D1／平台 callback |
| A08 | 兩連線 publisher 競爭與舊 claim 拒絕 | 跨 runtime 競爭 |
| A09 | 不同聯盟的 shared resource 鎖 | 正式路由 registry |
| A10 | 新 desired 使舊計算 superseded | 舊遠端 invocation 停止證據 |
| A11 | 薄 GS 簽章提示到 Python 候選、內容去重 | HTTP 接線與正式 trigger／排程 |
| A12 | timeout 隔離、回讀與重啟 | 真實 Sheets HTTP 核對 |
| A13 | 單一 fanout 失敗不波及其他聯盟 | 全聯盟彙總檢視 |
| A14 | auth 停止來源請求且保持 unknown | 真實 Session／provider 對應 |
| A15 | GS 固定排刀案例 | tie／guard 差異確認 |
| A16 | 排序 view 不改指定刀輸入 | 出刀 workbook renderer |
| A17 | unknown delivery 不盲重發 | Discord adapter／路由授權 |
| A18 | 預算原子保留、unknown 不退款 | 共用帳戶實際用量／降頻 |
| A19 | code affinity、local／hosted 邊界 | 同遠端備援協定／release distribution |
| A20 | budget 中斷與 checkpoint／恢復 | hosted profile／硬性隔離 |
| A21 | 缺片／取消不改結果、schema CAS | 真實 staging／merge／protection |
| A22 | 備份還原 fence、回滾另取 publisher | binary／降版與跨 runtime ownership |

## 報告生成

先以 `python -m pytest local_tests --junitxml <temporary.xml>` 產生真實本地測試紀錄，再以 `python -m executors.local.acceptance --junit <temporary.xml> --output <new-report.json>` 生成報告。輸出只可新檔；不啟動測試、不連外、不操作試算表。

application/acceptance.py 會將參數化案例映射回明確 pytest node ID，缺案例／skip 不視為通過，任何參數失敗都使對應需求失敗，suite_status 也保留未映射測試的失敗。每項必須保留 remaining，避免測試數量被誤用成正式驗收。

## 後續順序與外部依賴

仍可獨立開發：完整 grid renderer／保護與十一欄遷移、reference／補抓 planner、collector 分頁 checkpoint、路由 head／權限 registry、薄 GS 與 hosted entry 的離線契約、D1 adapter 契約、staging／chunk 清理、效能 profile 與全聯盟狀態檢視。不能因已有 22 項本地證據就 block 或標完成。

真正需要後續外部決策或驗證：正式工作簿與可編輯範圍盤點、正式服務 principal／key、實際 provider 的完整性欄位、帳戶共享免費用量、GS tie／安全 guard 差異採用、原 writer 停用證據、正式切換與 rollback ownership。這些不在目前授權的本地 mock 執行範圍內。
