"""Bounded read-only SQLite status snapshot for explicitly selected mock scopes."""

import hashlib
import json
from pathlib import Path
import sqlite3

from adapters.local_state import LocalStateStore
from application.raid_codec import restore_problem
from application.raid_inputs import is_raid_dependency_current
from application.tasks import AUTO_COMMANDS, StateConflict, TaskSpec, validate_identifier
from domain.snapshots import canonical_json
from domain.mock_workspace import MOCK_WORKSPACE_MARKER as MARKER

BLOCKED = frozenset({"failed", "auth_required", "capacity_blocked", "publish_uncertain"})


def read_system_status(directory, scope_ids) -> dict:
    if isinstance(scope_ids, str):
        raise ValueError("status scopes must be an explicit sequence")
    directory, scopes = Path(directory).resolve(), tuple(scope_ids)
    if not scopes or len(scopes) > 100 or len(set(scopes)) != len(scopes):
        raise ValueError("explicit unique bounded status scopes required")
    for scope in scopes:
        validate_identifier(scope)
    marker = directory / "mock-workspace.json"
    database = directory / "state.sqlite"
    if (not marker.is_file() or marker.stat().st_size > 4096 or not database.is_file()
            or canonical_json(json.loads(marker.read_text(encoding="utf-8"))) != canonical_json(MARKER)):
        raise ValueError("not an initialized mock workspace")
    connection = sqlite3.connect(database.as_uri()+"?mode=ro", uri=True, isolation_level=None, timeout=2)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        global_status = _global_status(connection, scopes)
        rows = []
        for scope in scopes:
            try:
                rows.append(_scope_status(connection, scope))
            except (ValueError, KeyError, TypeError, StateConflict):
                rows.append({"scope_id": scope, "is_available": False, "error_code": "status_input_invalid"})
        result = {"schema_version": 1, "is_mock": True, "is_read_only": True,
            "is_restore_fenced": (directory / "restore-lock.json").exists(), "scopes": rows, "global": global_status}
        result["status_hash"] = hashlib.sha256(canonical_json(result).encode()).hexdigest()
        return result
    finally:
        connection.close()


def _scope_status(connection, scope):
    input_row = connection.execute("SELECT scope_id,input_id,revision,roster_state FROM union_input_heads WHERE scope_id=?", (scope,)).fetchone()
    input_head = dict(input_row) if input_row else None
    raid_source_head = _ReadOnlyStatusState(connection).raid_source_version(scope)
    jobs = _bounded(connection, "SELECT j.*,d.family AS desired_family FROM desired_targets d JOIN jobs j ON j.fingerprint=d.fingerprint WHERE d.scope_id=? ORDER BY d.family", (scope,), 50)
    desired = []
    for row in jobs:
        spec = TaskSpec(**json.loads(row["spec_json"]))
        if (spec.scope_id != scope or spec.fingerprint != row["fingerprint"] or spec.command != row["command"]
                or spec.family != row["family"] or spec.family != row["desired_family"] or spec.code_sha != row["code_sha"] or spec.executor_type != row["executor_type"]):
            raise ValueError("status job identity differs")
        config_current = True
        config_id = spec.version_map.get("healthcheck_config")
        if spec.command in AUTO_COMMANDS and config_id is not None:
            head = connection.execute("SELECT h.config_id FROM config_versions v JOIN config_heads h ON h.config_type=v.config_type AND h.scope_id=v.scope_id WHERE v.config_id=?", (config_id,)).fetchone()
            config_current = head is not None and head[0] == config_id
        input_current = input_head is None or (spec.version_map.get("union_inputs") is None and spec.command not in ("recompute_healthcheck", "generate_raid_draft", "publish_raid_plan")) or (
            spec.version_map.get("union_inputs") == input_head["input_id"] and input_head["roster_state"] == "active")
        desired.append({"family": spec.family, "job_id": row["job_id"], "command": spec.command, "state": row["state"],
            "is_union_input_current": input_current,
            "is_job_current": _ReadOnlyStatusState(connection).is_job_current(row["job_id"]),
            "error_code": row["error_code"], "is_config_current": config_current, "code_sha": spec.code_sha,
            "executor_type": spec.executor_type, "versions": spec.versions, "result_id": row["result_id"], "lease_expires_at": row["expires_at"]})
    counts = dict(connection.execute("SELECT state,COUNT(*) FROM jobs WHERE scope_id=? GROUP BY state", (scope,)).fetchall())
    published = connection.execute("SELECT p.publication_id,p.commit_id,p.updated_at,j.job_id,j.family,j.fingerprint,j.code_sha,j.result_id "
        "FROM publications p JOIN jobs j ON j.job_id=p.job_id WHERE j.scope_id=? AND p.state='succeeded' ORDER BY p.updated_at DESC,p.publication_id DESC LIMIT 1", (scope,)).fetchone()
    last_published = dict(published) if published else None
    latest = _bounded(connection, "SELECT publication_id,commit_id,updated_at,job_id,family,fingerprint,code_sha,result_id FROM ("
        "SELECT p.publication_id,p.commit_id,p.updated_at,j.job_id,j.family,j.fingerprint,j.code_sha,j.result_id,"
        "ROW_NUMBER() OVER(PARTITION BY j.family ORDER BY p.updated_at DESC,p.publication_id DESC) AS ordinal "
        "FROM publications p JOIN jobs j ON j.job_id=p.job_id WHERE j.scope_id=? AND p.state='succeeded') WHERE ordinal=1 ORDER BY family", (scope,), 50)
    published_by_family = []
    for row in latest:
        item = dict(row)
        item["is_live_workbook_verified"] = False
        item["is_current_target"] = _ReadOnlyStatusState(connection).is_job_current(item["job_id"])
        item.pop("fingerprint")
        published_by_family.append(item)
    if last_published:
        last_published = next(item for item in published_by_family if item["publication_id"] == last_published["publication_id"])
    confirmed = connection.execute("SELECT h.confirmation_id,d.draft_id,d.problem_id,i.problem_id AS current_problem_id FROM raid_confirmed_heads h "
        "JOIN raid_confirmations c ON c.confirmation_id=h.confirmation_id JOIN raid_drafts d ON d.scope_id=c.scope_id AND d.draft_id=c.draft_id "
        "LEFT JOIN raid_input_heads i ON i.scope_id=h.scope_id WHERE h.scope_id=?", (scope,)).fetchone()
    confirmation = None if confirmed is None else {"confirmation_id": confirmed["confirmation_id"], "draft_plan_id": confirmed["draft_id"],
        "problem_id": confirmed["problem_id"], "is_stale": confirmed["problem_id"] != confirmed["current_problem_id"]}
    if confirmation is not None and not confirmation["is_stale"]:
        reader = _ReadOnlyStatusState(connection)
        data = reader.get_result(confirmed["problem_id"])
        if len(canonical_json(data).encode()) > 8*1024*1024:
            raise ValueError("status raid problem exceeds byte bound")
        confirmation["is_stale"] = not is_raid_dependency_current(reader, scope, restore_problem(data))
    batches = dict(connection.execute("SELECT is_accepted,COUNT(*) FROM report_send_candidates WHERE scope_id=? GROUP BY is_accepted", (scope,)).fetchall())
    return {"scope_id": scope, "is_available": True, "union_input_head": input_head, "raid_source_head": raid_source_head, "desired": desired, "historical_job_counts": counts,
        "last_published": last_published, "published_by_family": published_by_family, "raid_confirmation": confirmation,
        "report_batch_counts": {"prepared": batches.get(0, 0), "accepted": batches.get(1, 0)},
        "has_blocked_target": any(target["state"] in BLOCKED or not target["is_job_current"] for target in desired),
        "has_pending_target": any(target["state"] not in BLOCKED | {"succeeded", "superseded", "cancelled"} for target in desired),
        "is_all_desired_completed": bool(desired) and all(target["state"] == "succeeded" and target["is_job_current"] for target in desired)}


def _global_status(connection, scopes):
    placeholders = ",".join("?" for _ in ("common", *scopes))
    heads = _bounded(connection, f"SELECT config_type,scope_id,config_id,revision FROM config_heads WHERE scope_id IN ({placeholders}) ORDER BY scope_id,config_type", ("common", *scopes), 1000)
    references = _bounded(connection, f"SELECT scope_id,reference_id,revision FROM reference_heads WHERE scope_id IN ({placeholders}) ORDER BY scope_id", ("common", *scopes), 101)
    routes = _bounded(connection, f"SELECT scope_id,route_snapshot_id,revision FROM route_registry_heads WHERE scope_id IN ({placeholders}) ORDER BY scope_id", ("common", *scopes), 101)
    route_holds = _bounded(connection, "SELECT outbox_id,ordinal,generation,scope_id,principal_id,route_snapshot_id,is_started,start_deadline,started_at FROM authorized_route_leases ORDER BY scope_id,outbox_id,ordinal", (), 1000)
    holds = _bounded(connection, "SELECT p.publication_id,p.job_id,j.scope_id,p.state,p.commit_id FROM publications p JOIN jobs j ON j.job_id=p.job_id WHERE p.state!='succeeded' ORDER BY p.publication_id", (), 1000)
    locks = _bounded(connection, "SELECT resource_id,publication_id FROM resource_locks ORDER BY resource_id", (), 1000)
    budgets = _bounded(connection, "SELECT platform,period,limit_units,used_units,reserved_units FROM usage_budget ORDER BY platform,period", (), 1000)
    delivery = dict(connection.execute("SELECT state,COUNT(*) FROM report_parts GROUP BY state").fetchall())
    return {"config_heads": [dict(row) for row in heads], "reference_heads": [dict(row) for row in references],
        "route_heads": [dict(row) for row in routes], "route_holds": [dict(row) for row in route_holds],
        "publication_holds": [dict(row) for row in holds], "resource_locks": [dict(row) for row in locks],
        "is_write_blocked": bool(holds or locks), "delivery_part_counts": delivery,
        "has_unknown_delivery": delivery.get("delivery_uncertain", 0) > 0,
        "configured_usage_budgets": [dict(row) for row in budgets]}


def _bounded(connection, query, parameters, limit):
    rows = connection.execute(query+" LIMIT ?", (*parameters, limit+1)).fetchall()
    if len(rows) > limit or sum(sum(len(value.encode()) for value in row if isinstance(value, str)) for row in rows) > 8*1024*1024:
        raise ValueError("status scope exceeds row or byte bounds")
    return rows


class _ReadOnlyStatusState(LocalStateStore):
    """Reuse verified reads inside the existing query-only snapshot; never migrate."""

    def __init__(self, connection):
        self._connection = connection
