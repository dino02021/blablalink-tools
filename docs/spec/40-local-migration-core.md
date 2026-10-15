# 新試算表核心：本地實作範圍

新核心目前在 domain/healthcheck.py，尚未接入 GUI、headless jobs、GS、Google Sheets 或任何正式服務。

- 介面接受有效成員 ID、角色規則與以成員／角色 ID 為 key 的本地資料。
- 支援 T9、R0～R15、SR0～SR15、數值與加減門檻。
- 門檻空白為 not_applicable；缺少可比較值為 unknown。
- 角色按有效成員最高原始分正規化，再向下取整。
- 群組按角色整數分數乘權重加總，先依 GS Math.round 語意取整，再正規化。
- 輸出五屬性與輔助群組；採同分跳號的競賽排名。
- 不可用此計算函式單獨判定資料來源完整性；coverage 與發布安全由後續 application 層處理。
- local_tests 封鎖網路，使用合成案例；執行方式為 python -m pytest local_tests -q -p no:cacheprovider。

整體目標與尚未實作項目見 [遷移規格書](../plans/2026-10-13-free-sheets-python-migration-spec.md) 第 17 節。
