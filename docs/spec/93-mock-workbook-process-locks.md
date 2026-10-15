# Mock 工作簿的跨程序協作互斥

本批補上 92 發現的模擬器遺漏：原 read／modify／save 即使每次 JSON replace 原子，兩個程序仍可能各自以舊副本覆蓋另一方剛保存的人工欄或生成區。這是本地 mock backend 的實作修正，不宣稱 Google API 具有相同檔案鎖。

## 同一工作簿的序列化

adapters/mock_workbook_lock.py 以正規化實體工作簿路徑為 key，結合同程序 RLock 與 OS lock。Windows 使用 msvcrt 第一個 byte 的非阻塞鎖；POSIX 提供 flock 分支，目前實測環境為 Windows。

MockSheets 的 create／read、人工 inputs／tab／cell、layout／legacy／grid migration 採用與 apply 均在鎖內執行。完整 read／modify／save 被同一把鎖包住，publisher 讀目前人工欄後再保存，不把之前副本蓋回；人工 cell 修改也保留其他欄位與已發布生成區。

同一 thread 的巢狀讀取／修改可重入，不重複領 OS lock；其他 thread 或 process 必須等待。弱參考 registry 不把每個曾使用的暫存工作區永久留在全域 cache。

edit_inputs 仍是呼叫者明確要求的整份 inputs replacement，不替兩個 client 的舊整份 snapshot 猜測合併；編輯個別 cell 的介面則保留其他 cell。低階私有 _save 與外部直接寫 JSON 不在此協作契約內。

## 有界等待與程序結束

預設鎖取得最多等待兩秒，支援合作式計算檢查；逾時為 mock_workbook_lock_timeout，不先改工作簿。持鎖區域本身不是 TTL lease，不能因時間過去就讓第二個 writer 進入。程序結束由 OS 關閉 handle 釋放，不靠刪鎖檔推論停止。

每個工作簿的 .locks sidecar 只承載 OS 協作狀態，不保存資料／principal／憑證；檔案存在不代表 writer 活著，亦不能作 publication 的 writer-stop 證據。不可在持鎖程序仍可能存活時刪 sidecar 以繞過互斥。

MockSheets 路徑在建構時固定為絕對路徑。低階讀取可能建立 advisory sidecar；正式唯讀 status 入口不經此 mock book 路徑，不修改協調 DB 或工作簿內容。worker 的 restore fence 與 publisher authority 仍是另一層必要條件，檔案鎖不能取代它們。

## 備份與還原

backup 對每次 workbook bytes 讀取使用同一把鎖，並保留原前後 inventory／內容一致性檢查；不宣稱多個工作簿與人員編輯同時發生時有跨工作簿原子快照。sidecar 不進 archive，restore 按需要重建鎖狀態，原 restore-lock 仍保留。

## 本地證據與尚缺

local_tests/test_mock_workbook_lock.py 的 14 項案例包含兩個 adapter、真實子程序持舊讀取與 publisher 交錯、反向 parent 人工／child 發布、兩位編輯者各改一欄、四 thread 累計 read-modify-write、可重入、持鎖程序終止、有界 timeout／budget 不改資料、sidecar 備份排除與還原重建、錯等待參數。

子程序只載入合成 mock helper、篩選後環境與固定本地工作簿，沒有 provider 或正式資料。正式遠端多人編輯、CAS／partial range adapter、POSIX 實測、自動 rollback 與歷史清理仍在後續範圍。
