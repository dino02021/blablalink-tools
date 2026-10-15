"""Read-only cross-union status table; calculated is distinct from published."""

import hashlib
from domain.snapshots import canonical_json
from domain.view_grid import ViewCell, ViewTab


def render_system_status(status: dict) -> ViewTab:
    if status.get("is_mock") is not True or status.get("is_read_only") is not True:
        raise ValueError("read-only mock status required")
    if status.get("status_hash") != hashlib.sha256(canonical_json({key: value for key, value in status.items() if key != "status_hash"}).encode()).hexdigest():
        raise ValueError("status snapshot hash differs")
    if len(status["scopes"]) > 100 or any(len(scope.get("desired", ())) > 50 for scope in status["scopes"]):
        raise ValueError("status view exceeds scope bounds")
    columns = ("scope_id", "family", "desired_job_id", "desired_state", "config_current", "code_sha", "last_published_job", "published_current", "confirmation_id", "confirmation_stale", "global_write_blocked", "global_unknown_delivery", "error_code")
    labels = ("聯盟 ID", "工作類型", "最新要求", "最新狀態", "設定仍為目前版本", "程式版本", "最後成功發布", "發布符合最新要求", "排刀確認", "確認已過期", "共用發布被占用", "共用訊息狀態未明", "錯誤代碼")
    columns = columns[:-1] + ("union_input_id", "roster_state", "union_input_current", columns[-1])
    labels = labels[:-1] + ("目前名單輸入版本", "名單狀態", "工作符合目前名單", labels[-1])
    rows, ids = [tuple(ViewCell(label, "header") for label in labels)], ["header"]
    for scope in status["scopes"]:
        if not scope["is_available"]:
            rows.append((ViewCell(scope["scope_id"]), *(ViewCell() for _ in range(len(columns)-2)), ViewCell(scope["error_code"], "failed")))
            ids.append(canonical_json([scope["scope_id"], "unavailable"]))
            continue
        for target in scope["desired"] or [None]:
            published = scope["last_published"]
            confirmation = scope["raid_confirmation"]
            family = target["family"] if target else ""
            last = published if published and published["family"] == family else None
            values = (scope["scope_id"], family, target["job_id"] if target else None, target["state"] if target else "no_desired_target",
                target["is_config_current"] if target else None, target["code_sha"] if target else None,
                last["job_id"] if last else None, last["is_current_target"] if last else None,
                confirmation["confirmation_id"] if confirmation else None, confirmation["is_stale"] if confirmation else None,
                status["global"]["is_write_blocked"], status["global"]["has_unknown_delivery"], target["error_code"] if target else None)
            head = scope.get("union_input_head")
            values = values[:-1] + (head["input_id"] if head else None, head["roster_state"] if head else "unmanaged", target["is_union_input_current"] if target else None, values[-1])
            rows.append(tuple(ViewCell(value, "failed" if index == 15 and value else "unknown" if (index in (9, 10, 11) and value is True) or (index == 14 and value is False) else "data") for index, value in enumerate(values)))
            ids.append(canonical_json([scope["scope_id"], family]))
    return ViewTab("system_status", "聯盟系統狀態", columns, tuple(ids), tuple(rows), frozen_rows=1, frozen_columns=2)
