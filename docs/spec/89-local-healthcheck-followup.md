# 本地健檢後續調度 policy 與持久 tick

LocalHealthcheckWorkflows／0014 migration 提供逐 scope 的可信本地 Python API。這一批不接背景 scheduler 或自動 worker hook；呼叫 reconcile_healthcheck_workflow 才執行一次有界 tick，不直接呼叫 CollectionPort 或發布。

## 固定 policy 與採用

mock_healthcheck_workflow 固定 code、content reference ID、baseline raw ID、整份 immutable mock script、config scope（common 或該聯盟）、is_enabled 與最多 1..3 輪補抓。全部來源須同 scope／reference 並有完整 role universe；script 必須為 88 的有效合成來源。最多 100 個配置 scope。

configure_healthcheck_workflow 以明確 policy ID 確認、actor、expected revision、request 採用；head、初始 state、receipt／request 同交易保存，等待 unresolved publisher。精確重播只回 receipt，不重設進度或倒回 head。相同內容以新的 expected revision 重新配置屬明確的新配置，會重設 baseline／進度；停用只停止排新工作，不召回已接受工作。

state 保存目前 raw、cycle、輪數、pending collection、最後 collection／health job、plan key 與安全狀態／錯誤代碼，綁 policy ID 並有內容 checksum。相同 state 不增加 revision／更新時間。

## 一次 tick 的判斷

- 核對 code affinity、policy、目前 union-input head 及指定 config head。未有名單／設定就等待；inactive 不排新工作並清除 pending scheduler pointer；reference 不符等待明確轉換，不重標 raw。
- 未完成 collection 維持等待，不另排同一需求；auth／failed／capacity 等狀態不自動重送或重設嘗試次數。名單變更後舊 pending 依原工作 fence 失效，新 cycle 才重新規劃。
- 成功 collection 必須具有固定 base raw、collection inputs、當代 generation 的 fresh raw／training／checkpoint observation。重新以原 base／fresh／名單建立 merged raw 並重做 normalizer，全部 ID 相同才前進 raw pointer。不能相信 result 的 ready 旗標就發布。
- 使用目前 config／名單／epoch 對目前 raw 重建 reuse plan。有 blocker 就明列狀態；有缺口則從 policy script 投影所需成員，建立固定 supplementary job。缺 mock 成員資料就等待，不補造數值、不 fallback 到其他 provider。
- 固定需求 ready 時封存 training，將目前 config／名單／training 排為 desired healthcheck。collector 本身不發布；worker 仍預設 compute-only，明確 mock publish 才會寫工作簿。

所有排隊與 state 更新同一 BEGIN IMMEDIATE；兩個 reconciler 不能各自增加輪次／排重複工作。計算有合作式 budget；超限整次回滾，不留下假完成或半份調度。

## 版本、輪次與 owner

cycle 綁 policy／union-input／config 的 ID 與各自 revision，包含返回相同舊內容的版本變化，不能只看 payload hash。collection intent 與 health request 固定 cycle／plan／round，精確 retry 不另建 job。設定回到原內容時，新的 revision 仍會重新採用正確 desired，可沿用同一 immutable 計算。

同一 cycle 最多三輪補抓。complete source observation 仍有缺口時，可依新 raw 自動排下一輪；到上限即 capacity_blocked／supplementary_round_limit，重啟不清零，避免完整但缺欄的來源造成無限新工作。

若本 tick 已排的 health job 在相同 cycle 被外部 desired owner 換掉，回 waiting_for_owner／workflow_desired_conflict，保留對方 head。不能為了持續執行而反覆搶回工作。需要明確新 policy 或真正來源 head 改版才進入新協作週期。

publisher busy 時可保存已完成的新觀測及 waiting_for_publisher，之後只重試排重算，不重抓。已排工作的目前性與 publisher fence 維持既有契約。

## 唯讀狀態與證據

read_system_status 增加 healthcheck_workflow metadata，核對 policy／state checksum，壞資料只隔離該 scope；不 migration、不 tick、不排工作。typed 狀態表增加調度狀態與錯誤，共 18 欄，layout revision 隨之改變，未接遠端共享表。

local_tests/test_healthcheck_workflows.py 的 20 項 mock 案例驗證補抓→重算→實際 MockSheets 發布及人工 tab 保留、門檻更新不重抓、雙連線、重啟／重播、owner 衝突、名單 rejoin／inactive、假觀測拒絕、budget／DB rollback、三輪上限、auth 不重送、publisher 等待、缺 fixture、ABA revision 與唯讀／壞 scope 隔離。

尚需有界 worker／tick runner 接線、公平 scope 輪巡、raid 採用後調度、hosted／D1 契約、正式 provider 與服務身分。本機 API 不等於已部署排程；沒有使用正式資料或試算表。

91 已加入獨立有界 runner、持久公平 cursor 與 worker hook；直接 API 仍可單獨使用。worker 預設最多十次 scope 選取，0 可停用本次自動 tick；沒有建立正式 scheduler。
