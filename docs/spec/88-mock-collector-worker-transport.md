# 本地 worker 的固定 mock collector transport

worker 現在支援普通與 87 的成員補抓 collection，但只領取明確綁定 mock_collection_script 的工作。沒有真實 provider factory、URL、Session 或憑證載入分支。

## 合成腳本

adapters/mock_collection_script.py 的 script 為 immutable union_inputs 文件，固定 scope／reference、is_mock=true、schema 1，以及最多 300 人／900 個 member-source。每個 source 只允許 member、source、outcome、pages；outcome 為 pages／auth_required／unavailable。

每個 source 最多 100 頁／10,000 rows，每頁最多 1 MiB，整份文件最多 16 MiB。page 僅允許 rows、total、is_options_complete；資料列必須等於既有 raw whitelist 結果，未知欄位不能藏進腳本。此格式不接受 endpoint、headers、回呼或錯誤內文。

MockScriptCollection 在呼叫前重建驗證整份文件，要求 scope／reference 相符並且剛好涵蓋該 collection inputs 的成員三個 source。分頁 cursor 與 revision 在記憶體由 script ID／身分／頁碼導出，不在文件保存實際 provider token。script 不可變，因此此 mock adapter 可明確保證 stable snapshot revision；不能將此保證套到真實 provider。

## 固定工作與能力領取

TaskSpec.versions 的 mock_collection_script 固定腳本 ID，保留於 fingerprint／backup／retention roots。supplementary_task_spec 增加可選 mock_script_id；直接 Python CollectionPort 呼叫仍可不綁腳本，但 worker 會跳過未綁定 collection 工作。

LocalStateStore.claim 增加 required_versions command 能力篩選，僅供 local queue。命令與 version key 皆經有界驗證，以 SQL parameter／json_each 在候選 LIMIT 100 前篩選，避免大量不支援工作讓可執行工作永久排不到。篩選只表示 executor 能力，不是外部授權；未匹配者不領 lease、不增加 attempt、不改失敗狀態。

worker 的 collection 分支只建 MockScriptCollection，依 collection_kind 選普通或 supplementary executor。code affinity、local executor、標記 workspace、restore fence、每次 1..100 工作、合作式預算及 native compute-only 邊界不變。

## 執行效果

收集只保存 raw／training／checkpoint／result，不自動生成健檢工作、確認候選、發訊息或發布。即使 worker 顯式開啟 mock publish，collector 成功也不建立 publication。普通 collector 與補抓保留各自的完整性／auth／followup 契約。

不可變腳本代表一份固定合成觀測；要模擬後來資料改變，需明確新腳本／工作。87 的直接 port retry 案例另驗證 partial 後來源變動；本文件不宣稱真實 provider 的 resume／revision 語意已成立。

## 本地驗證與尚缺

local_tests/test_mock_collection_worker.py 的 17 項案例驗證 worker 普通／補抓、native child 無 parent port、未綁定工作保持 queued／attempt=0、錯 scope 拒絕、110 筆未綁工作不阻擋可執行工作、壞 capability、分頁、未知／secret 欄位及無 publication。

仍需名單／設定採用後的後續需求調度、hosted collector 契約、role／option 細粒度與正式來源完整性；目前沒有對正式環境接線。

89 補上採用後的本地 durable tick：依目前 config／名單補抓或排重算、最多三輪、遇外部 desired owner 停在明確衝突。尚未接有界 worker tick runner／公平輪巡，沒有正式排程。
