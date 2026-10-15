# 本地快照分片

domain/chunks.py 定義傳輸 codec；adapters/local_chunks.py 使用獨立本地 SQLite 暫存。沒有網路入口或遠端儲存。

## 契約

- canonical JSON 文件以 UTF-8 編碼，再用 zlib 壓縮；禁止 pickle。
- ChunkManifest schema_version=1，包含物件／內容 SHA-256、原始與壓縮大小、codec、依序排列的分片 hash。
- 分片最大 32 KiB，最多 512 片；原始與壓縮內容各限制 16 MiB。大小與版本不接受 bool。
- object_id 必須等於原始文件內容 hash；分片數須符合壓縮大小。
- 每片核對 ordinal、精確大小與 hash。支援任意順序、相同內容重播及 missing 查詢。
- 同一 object_id 的 manifest 固定，不允許改寫；client 必須先沿用服務端已有 manifest，不能混用不同壓縮結果。
- seal 核對全部序號、hash、解壓大小、stream 結束、無額外資料及快照 envelope，成功才設定 sealed。
- 解壓輸出上限為宣告大小加一，不以無上限解壓判斷大小。
- read 只接受 sealed，且每次重新驗證內容；封存標記不能掩蓋後續損毀。
- 完整文件可再交給 LocalStateStore.put_document。原始 Document 建立流程仍可直接用於本地計算；此 transport 尚未成為唯一輸入入口。

## 驗證與界線

local_tests/test_chunks.py 使用合成多分片文件，驗證反向上傳、重播、重啟、缺片、損毀、尾隨資料、hash／大小錯誤、解壓限制及封存後匯入狀態庫。

目前暫存 schema 僅供本地驗證；尚無 D1 migration、HTTP body／base64 封裝、帳戶儲存配額、引用保留與清理工作。此快照 transport 也不等於工作簿分片 staging，後者仍須 layout／切換驗證。
