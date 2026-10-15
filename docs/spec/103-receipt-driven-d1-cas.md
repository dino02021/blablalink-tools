# 103：以 receipt 驅動的低寫入 D1 CAS 候選

## 設計決定與界線

102 證明暫存 guard 會增加 written rows。本批比較另一個獨立 head 契約：以 receipt INSERT 作為唯一 mutation，資料庫 trigger 在同一 statement 內驗證 CAS、更新 head；第二個 prepared statement 只讀原 receipt。保留原七步版作語意與成本比較基準，不接現有 LocalStateStore 或正式環境。

此候選只含三個小型 control tables：heads、receipts、barriers，採 WITHOUT ROWID，以 string／composite primary key 儲存資料。這適合目前的短小 control records；不直接推廣到大型 snapshot／blob／event 自增表。[SQLite WITHOUT ROWID](https://www.sqlite.org/withoutrowid.html)

## 原子語意

共同 HeadCasIntent／signature 不變。BEFORE INSERT trigger 先判斷相同 request 的全部欄位；完全相同以 IGNORE 回原決定，異意圖以 ABORT 拒絕。新 request 才檢查 scoped／global barrier 與目前 revision。

AFTER INSERT trigger 更新 head，再核對完整 revision／version／code；head 未證明寫入就中止 receipt。head 與 receipt 在單一 mutation statement 內原子完成，不需要跨兩次 API 維持開啟 transaction。回應遺失時仍固定原 request 恢復；舊 receipt 重播不 rewind 新 head，也不因新 barrier 重新啟動工作。

build_receipt_cas_batch 只產生 mutation 加 receipt read 兩個外部 prepared statements；verify_receipt_cas_result 必須收到完整兩項成功與精確 receipt。單純 INSERT 回傳成功或零 changes 不代表已讀到固定決定。觸發器內部仍有 queries，不能把兩個外部 statements 當完整平台配額模型。

## Schema pin 與故障拒絕

SCHEMA_ID 固定三張 table 與兩個 trigger 的定義。mutation 表達式在執行 trigger 前比對 sqlite_master 中這五個物件的完整 SQL，並要求這三個表只存在原兩個 triggers。缺失、修改、額外 trigger 或 table drift 皆不能發出新 receipt。

失配分支故意以內建 json('schema_mismatch') 產生 SQL error，在任何 row／trigger mutation 前中止；不以零列或可能已失效的 NOT NULL 條件當作拒絕證據。這依賴實際 D1 已驗證的 JSON extension 與 schema 查詢行為。[D1 SQL statements](https://developers.cloudflare.com/d1/sql-api/sql-statements/)

schema fixture 改接受明確完整 statements array，trigger body 不再用 semicolon split 破壞。此 array 僅來自可信靜態 Python 常數，不是任意外部 migration parser 或 SQL HTTP 入口。

正式 migration 必須保持預期 DDL／release；不同但看似等價的 SQL 文字也會 fail closed。schema pin 不是對 DB 管理者惡意修改的安全邊界，額外 index 與其他 tables 的成本仍須另盤點。positive receipt lookup 可讀歷史決定，但不能藉此採用新 head 或跳過新 mutation 的 schema gate。

## 同 fixture 的實際比較

`executors/local/d1_receipt_runtime.py` 依序啟動兩個相同 pinned Miniflare／workerd profile，使用相同 request、scope、family、version、code、actor，比較初次 CAS、stale、更新及舊 replay，最後核對 head revision=2、兩份 receipt。兩次 SDK children 皆確實退出，Worker outbound 探針都本地攔截。

| 本地單次操作 | 七步 guard batch：讀／寫 rows | receipt 驅動：讀／寫 rows |
| --- | --- | --- |
| 新 CAS | 10／9 | 16／2 |
| 直接 batch replay | 11／5 | 15／0 |
| 既有 positive lookup | 1／0 | 可共用同一 lookup，另次 prototype 未重測此路徑 |

新設計降低寫入，代價是更多 schema 讀取。僅作目前 fixture 的候選選擇，不宣稱所有工作負載更快、更便宜或可容納固定聯盟數。實際帳戶、失敗 queries、setup／schema migration、完整 jobs／locks／objects 及其他專案尚未計入，is_complete_account_usage=false。

共用 sum_d1_query_usage 只接受有界、完整成功的 query metadata；missing／null／boolean／不精確或超範圍 counter 均拒絕，不把未知成本當零。

## 證據與後續

local_tests/test_receipt_d1_contract.py 十六項 SQLite 案例與 local_tests/test_receipt_d1_runtime.py 十項真實 Miniflare 案例，驗證 schema drift、零 head write、並行／同 request、barrier、single mutation response loss 與低寫入比較。另九項共用 usage metadata 回歸，Windows-only runtime 的 skip 仍使對應驗收不完整。

實際 CLI `python -m executors.local.d1_receipt_runtime --output <new-json>` 產生 [2026-10-16-receipt-d1-comparison.json](../validation/2026-10-16-receipt-d1-comparison.json)。不覆寫既有證據，不連帳戶。

後續 D1 adapter 優先評估 receipt-driven mutation，但尚需完整 jobs／leases／publisher、immutable object chunks、account admission、auth、跨 release／migration 與正式平台驗證。兩個原型都不是現行業務協調器，不能拿 standalone head 通過當成整套系統已移轉。
