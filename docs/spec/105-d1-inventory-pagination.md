# 105：D1 分片清單有界分頁

## 問題與行為

最大 manifest 含 512 個 SHA-256；若連同全部 chunk inventory 回傳，即使每筆 metadata 很小，整體仍可能超過 64 KiB stdout。不能只驗證單片 binary 封包大小。

list_chunks 使用 ordinal keyset cursor 與固定 LIMIT 64。missing 逐頁讀取，最多 8 頁；每頁核對欄位、嚴格遞增 ordinal、manifest hash 與精確大小，拒絕重複、倒序、超頁、未知欄位及 bool 冒充 size。第一頁可與 manifest 同 batch，最大形狀編碼仍小於 64 KiB。

Inventory 是缺片提示，不是內容驗證；中途新補入較小 ordinal 可能保守回報缺片，下次重查即可。seal 仍必須 Python 讀回實際 bytes 並解碼驗證，不能以 missing()==() 替代。

## 本地驗證

新增完整 512 片、跨頁缺片、六種 malformed pages 與 cursor 型別邊界。實際 Miniflare fixture 儲存 512 個 32768-byte BLOB，再由不同 owned SDK processes 分頁讀 inventory。

此最大形狀 fixture 故意使用 zeroblob，並不是合法 zlib；只證明持久 D1 inventory 與輸出界限，未 seal、不能被計算讀取，不宣稱完整 16 MiB 物件驗收。

## 尚待完成

完整大型合法物件的高效率有界會話、seal/read 邊界、不同壓縮編碼、回覆遺失及物件配額／retention／遠端授權仍待後續工作。正式環境不接線。

本批 focused inventory 與既有 object port 合計 19 項通過；完整 suite 將與大型 session 一同回歸。

最新完整回歸：1,096 項通過，22 項本地證據通過，is_production_accepted=false。包含最大 inventory、16 MiB 合法物件、同 DB 共用 session 與原成本比較；先前失敗仍保留上述紀錄，不以重試自動掩蓋。完整 D1 業務 coordinator 尚未完成。
