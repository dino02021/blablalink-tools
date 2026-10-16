# 120：Mock D1 升級協調與 marker 恢復

## 必須補齊的邊界

原本 spec 118 的 SQL steps 與 spec 119 的逐 batch readiness 沒有高階協調器。DB transaction、OS lock 與 JSON marker 不屬於同一交易；中斷時不能以任何一個片段單獨宣告完成。後續本地實作見 spec 121–123。

## 協調契約

1. 僅處理已初始化 spec 118 authority／history 的 mock versioned workspace。拒絕正式 binding、Root SQLite、錯誤 marker、redirected path 與所有 restore-lock.json；這不是 restore 解封工具。
2. 取得同一 SDK byte-range 的獨占鎖並排空合作 SDK，重新核對 marker／fences。只允許一個匹配 plan／base revision／base epoch 的升級；不同 request 不可覆寫既有 maintenance journal。
3. 專用 maintenance capability 綁工作目錄、完整 plan、目前 thread、持有中的 lock lifetime，以及有界可信 SQL frame digest allowlist。正常 SDK 入口不新增 bypass bool。capability 離開區段、換 thread、改 frame 或指向其他目錄均拒絕。
4. 在修改 readiness 前先讀 D1 authority／完整 prefix 與目前 schema，驗證所有欄位／epoch。錯誤 plan 不能先寫 fence 讓正常工作目錄被無端鎖住；可先在獨占區段允許僅 metadata reads，實際 mutating steps 必須另核對已存在的匹配 maintenance journal。
5. 先排除 active／unknown business holds，再落盤固定 journal，然後執行 begin／逐版本／finish。compute running 即使 TTL 過期也不能在 migration 隱式 recover；hosted、publisher、resource locks、outbox unknown 不因本地 SDK 全停就視為已終止。尚無對應 quiescence policy 的 hold 類型先拒絕。
6. 每個步驟回覆不明時停止並保留 journal／DB migrating；下次以同一 plan 讀 authoritative D1 receipts，已提交版本不重播。歷史 prefix、目前 phase、revision、schema 與 epoch 必須一致，不能只用物件存在判定已完成。
7. 全部 steps 完成後，先再次核對 ready／最終 history／schema，再用 owned temporary file 原子替換 marker 為完整 business＋ledger schema identity。marker 尚舊時 normal access 必須因 maintenance fence 拒絕；marker 已新但 journal 尚在也同樣拒絕。
8. 只有 marker 與 D1 最終 proof 都符合才移除該 plan 的 maintenance journal。中斷於 marker 更新之前或之後，皆可由相同 plan 恢復；不回退 metadata、不覆寫不相符 journal、不清理非本次建立的未知檔案。
9. 已完整完成的相同 plan replay 只讀核對，不再提高 epoch、不執行 DDL 或 history writes。所有 native SDK tree／control frame／50 queries／100 binds 界限不變。

## 後續驗收

多版本保留資料且新 versioned session 可重開、舊 session 在啟動 SDK 前因 marker 或同 batch epoch 拒絕；begin／中間 step／finish 回覆遺失後從 D1 續跑；marker 替換前後中斷恢復；兩個 migrator 排他；錯 plan／checksum 不改 marker／DB／journal；busy／unknown holds 不遷移；capability 過期／跨 thread／改 body 拒絕；restore clone 完全不能啟動 SDK。

所有測試使用新建 temporary mock storage，維持不接正式環境。這份文件是本地 additive 協調器的驗收契約，不能擴稱正式切換或完整 Root backend 已完成。

## 已核對的 quiescence 差異

目前 compute_jobs 的 running 是有效租約狀態；contract_barriers 的任何 is_blocked=1 代表 scope／global barrier，不能因 SDK 都停止就略過。兩者需在已排空 SDK 的獨占區段另外查驗。歷史 claim receipts 或 computed 結果本身不等同仍持有執行租約。

協調器須登錄目前可識別的 object／head／compute／checkpoint／recovery table 契約與 hold policy；未知業務 hold schema 不猜測為 idle。合成 fixture 的 mock_* namespace 可有明確測試用 policy，不能將這份規則套到正式 provider 資料。未來新增 publisher／resource／outbox 模組時，必須同批追加 policy 與 active／unknown cases，不能只把表名加進 allowlist。

## 本地實作進度

Hold policy 與限定維護授權已見 spec 121／122；高階流程、逐版本續跑與 marker commit 見 spec 123。Journal 遺失時也以 D1 完整 proof 決定是否重建本輪相同 fence；錯 journal／restore clone 不解除。首輪 24 項 pure／實際 SDK 協調案例已通過，追加情境與完整回歸繼續驗證；未將本地 additive 證據擴稱正式整體切換。
