"""Single synthetic collaboration scenario across the local migration system."""

import argparse
from contextlib import closing
from dataclasses import asdict, replace
import json
from pathlib import Path

from adapters.local_authorized_outbox import LocalAuthorizedOutbox
from adapters.local_backup import create_backup, restore_backup
from adapters.local_raid_plans import LocalRaidPlans
from adapters.local_references import LocalReferences
from adapters.local_retention import LocalObjectRetention
from adapters.local_routing_registry import LocalRoutingRegistry
from adapters.local_system_status import read_system_status
from adapters.mock_collection import MockCollection
from adapters.mock_discord import MockDiscord
from adapters.mock_sheets import MockSheets
from application.collection import run_collection_job
from application.compute_budget import ComputeBudget
from application.delivery import send_next
from application.reference_catalog import capture_reference, read_reference
from application.report_delivery import compile_report_candidate
from application.routing_registry import RoutingAdministrator, SenderGrant
from application.sheet_healthcheck import reconcile_standard
from application.tasks import StateConflict, TaskSpec
from application.training_reuse import plan_training_reuse
from domain.healthcheck import ELEMENTS
from domain.mock_workspace import MOCK_WORKSPACE_MARKER
from domain.raid import FixedInputs, RaidMember
from domain.raid_planner import RaidProblem
from domain.snapshots import MemberKey, canonical_json, capture_document
from domain.view_grid import restore_workbook_view
from executors.local.isolated import run_isolated_worker
from executors.local.worker import run_worker

CODE = "integrated-mock-v1"
SCOPE = "union_scenario_mock"
MEMBERS = (MemberKey("area_mock", "member_a"), MemberKey("area_mock", "member_b"))


def run_scenario(directory: Path) -> dict:
    directory = Path(directory).resolve()
    if directory.exists():
        raise ValueError("integrated scenario requires a new workspace")
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "mock-workspace.json").write_text(canonical_json(MOCK_WORKSPACE_MARKER), encoding="utf-8")
    sheets = MockSheets(directory / "workbooks")
    sheets.create("standard_scenario", "standard_v1", {"standard_rows": _standard_rows(5)})
    sheets.create("output_scenario", "output_v1", {"manual_note": "keep"})
    sheets.set_manual_tab("output_scenario", "manual", "人工備註", [["before"]])
    with closing(LocalReferences(directory / "state.sqlite")) as references:
        reference = capture_reference("common", _time(0), ("role_mock",), {"10001": "attack"},
            (("模擬角色", "role_mock"),), (("攻擊", "attack"),))
        candidate = references.create_reference_candidate(reference, None, CODE)
        references.publish_reference("reference", candidate, candidate, None, "mock_maintainer", CODE)
    catalog, prefixes = read_reference(reference)
    port, collected, first_collection_calls = _collect(directory, catalog, prefixes)
    with closing(LocalRoutingRegistry(directory / "state.sqlite")) as store:
        first_candidate = reconcile_standard(store, sheets, "standard_scenario", "common", _time(0), catalog.reference)
        config = store.candidate_document(first_candidate)
        inputs = capture_document("union_inputs", SCOPE, _time(0), catalog.reference.version,
            {"members": [{"member": asdict(member), "display_name": "=同名"} for member in MEMBERS],
                "healthcheck_route": {"resource": "output_scenario", "layout_revision": "output_v1"}})
        store.put_document(inputs)
        training = store.get_document(collected["training_snapshot_id"])
        raw = store.get_document(collected["raw_snapshot_id"])
        reuse = plan_training_reuse(config, raw, catalog.reference, prefixes, MEMBERS, {member.key: "=同名" for member in MEMBERS}, CODE)
        _require(reuse.is_ready and reuse.training_id == training.snapshot_id, "initial reuse projection differs")
        spec = TaskSpec("recompute_healthcheck", SCOPE, CODE, {"healthcheck_config": config.snapshot_id,
            "training": training.snapshot_id, "union_inputs": inputs.snapshot_id})
        store.publish_candidate(first_candidate, (spec,))
        first_job, first_execution = _compute_and_publish(directory, sheets)
        first_generated = sheets.read("output_scenario")["generated"]
        _require(first_generated["config_id"] == config.snapshot_id, "initial standard publication differs")
        calls_before = len(port.calls)
        sheets.edit_inputs("standard_scenario", {"standard_rows": _standard_rows(9)})
        sheets.edit_cell("output_scenario", "manual", 0, 0, "collaborator changed")
        pending = reconcile_standard(store, sheets, "standard_scenario", "common", _time(3), catalog.reference)
        _require(sheets.read("output_scenario")["generated"] == first_generated, "unconfirmed draft replaced output")
        next_config = store.candidate_document(pending)
        store.publish_candidate(pending, (replace(spec, versions={**spec.version_map, "healthcheck_config": next_config.snapshot_id}),))
        next_job, next_execution = _compute_and_publish(directory, sheets)
        generated = sheets.read("output_scenario")["generated"]
        view = restore_workbook_view(generated["view"])
        _require(view.config_id == next_config.snapshot_id and view.training_id == training.snapshot_id, "recompute changed data source")
        _require(len(port.calls) == calls_before, "standard edit refetched training")
        _require(sheets.read("output_scenario")["manual_tabs"]["manual"]["cells"][0][0]["value"] == {"stringValue": "collaborator changed"}, "human tab changed")
        reports = _send_reports(store, directory, next_job, catalog.reference.version)
        health_result = store.get_job(next_job).result_id
        raid, raid_execution = _raid(directory, inputs, generated, health_result)
        status = read_system_status(directory, (SCOPE,))
        _require(status["scopes"][0]["is_all_desired_completed"] and not status["global"]["is_write_blocked"], "system did not settle")
        _require(not status["global"]["route_holds"] and not status["global"]["has_unknown_delivery"], "sender did not settle")
    retention = _retain(directory)
    backup = create_backup(directory, directory / "backup.zip", CODE)
    restored = directory / "restored"
    restore_backup(directory / "backup.zip", restored, backup["backup_id"], CODE)
    restored_status = read_system_status(restored, (SCOPE,))
    _require(restored_status["is_restore_fenced"] and restored_status["scopes"] == status["scopes"], "restored state differs")
    try:
        run_worker(restored, CODE)
    except ValueError:
        pass
    else:
        raise StateConflict("restored clone acquired execution authority")
    result = {"schema_version": 1, "is_mock": True, "network_providers": [], "code_sha": CODE,
        "reference_id": reference.snapshot_id, "reference_version": catalog.reference.version,
        "raw_snapshot_id": raw.snapshot_id, "training_snapshot_id": training.snapshot_id,
        "collection_checkpoint_id": collected["collection_checkpoint_id"],
        "first_config_id": config.snapshot_id, "final_config_id": next_config.snapshot_id,
        "first_job_id": first_job, "final_job_id": next_job, "generated_view_id": view.view_id,
        "collector_calls": first_collection_calls+calls_before, "collector_calls_after_standard_edit": first_collection_calls+len(port.calls),
        "collection_attempts": 2,
        "is_unconfirmed_draft_isolated": True, "is_manual_tab_preserved": True, "reports": reports,
        "compute_invocations": [first_execution, next_execution, raid_execution],
        "raid": raid, "is_system_settled": True, "retention_deleted_count": len(retention),
        "backup_id": backup["backup_id"], "is_restored_fenced": True,
        "scope_status": status["scopes"][0]}
    serialized = canonical_json(result)
    (directory / "scenario-result.json").write_text(serialized, encoding="utf-8")
    return json.loads(serialized)


def main():
    parser = argparse.ArgumentParser(description="Entirely synthetic local Sheets/Python collaboration scenario")
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    result = run_scenario(args.workspace)
    print(json.dumps(result, ensure_ascii=False))
    return 0


def _collect(directory, catalog, prefixes):
    with closing(LocalRoutingRegistry(directory / "state.sqlite")) as store:
        inputs = capture_document("union_inputs", SCOPE, _time(0), catalog.reference.version,
            {"collection_kind": "training_inputs", "members": [asdict(member) for member in MEMBERS],
                "role_ids": list(catalog.reference.role_ids), "function_prefixes": prefixes})
        store.put_document(inputs)
        job = store.submit_job("collect", TaskSpec("refresh_roster_and_training", SCOPE, CODE,
            {"collection_inputs": inputs.snapshot_id}, family="collection", intent_id="mock_collection"))
        first_pages = _pages(catalog.reference.version)
        first_pages[(MEMBERS[1].key, "details", None)] = TimeoutError()
        first_pages[(MEMBERS[1].key, "effects", None)] = _page(MEMBERS[1], "effects", [], catalog.reference.version)
        first_port = MockCollection(first_pages, True)
        first = run_collection_job(store, store.claim("collector", CODE), first_port, _time(1), ComputeBudget())
        _require(first.state == "retry_wait" and len(store.collection_observations(job.job_id)) == 1, "partial collection was accepted")
        store.reroute(job.job_id, "local", CODE)  # explicit synthetic operator resume, no real-time sleep
        port = MockCollection(_pages(catalog.reference.version), True)
        completed = run_collection_job(store, store.claim("collector", CODE), port, _time(2), ComputeBudget())
        _require(completed.state == "succeeded" and len(store.collection_observations(job.job_id)) == 2, "collection resume failed")
        return port, store.get_result(completed.result_id), len(first_port.calls)


def _compute_and_publish(directory, sheets):
    before = sheets.read("output_scenario")["generated"]
    isolated = run_isolated_worker(directory, CODE, "scenario-compute", seconds=30)
    _require(isolated["outcome"] == "completed" and len(isolated["child_result"]["processed"]) == 1, "isolated computation failed")
    processed = isolated["child_result"]["processed"][0]
    _require(processed["state"] == "computed" and sheets.read("output_scenario")["generated"] == before, "compute child published output")
    published = run_worker(directory, CODE, "scenario-publisher", max_jobs=1, is_publish_enabled=True)
    _require(published["processed"][0]["job_id"] == processed["job_id"] and published["processed"][0]["state"] == "succeeded", "staged publication failed")
    return processed["job_id"], _execution_receipt(isolated)


def _send_reports(store, directory, job_id, reference_version):
    admin = RoutingAdministrator("mock_maintainer", (SCOPE,))
    routes = capture_document("union_inputs", SCOPE, _time(4), reference_version, {"routing_kind": "report_routes", "routes": [
        {"member": asdict(member), "target_thread": "mock_thread", "mention_user_id": str(111+index)} for index, member in enumerate(MEMBERS)]})
    store.publish_route("route", routes, routes.snapshot_id, None, admin, CODE)
    grant = SenderGrant(SCOPE, "mock_sender", ("mock_thread",), ("111", "112"))
    store.publish_sender_grant("grant", grant, grant.grant_id, 0, admin, CODE)
    candidate = compile_report_candidate(store, job_id, routes.snapshot_id, "mock_send")
    _require(candidate is not None and len(candidate.deliveries) == 2, "report cohort differs")
    with closing(LocalAuthorizedOutbox(directory / "state.sqlite", "mock_sender")) as outbox, closing(MockDiscord(directory / "messages.sqlite")) as provider:
        outbox.prepare_batch(candidate)
        ids = outbox.confirm_batch("send_confirm", candidate.candidate_id, candidate.candidate_id)
        for spec in candidate.deliveries:
            for _ in spec.parts:
                _require(send_next(outbox, provider, spec.outbox_id, "mock_sender_worker"), "mock report part did not complete")
        _require(outbox.confirm_batch("send_confirm", candidate.candidate_id, candidate.candidate_id) == ids, "send replay changed IDs")
        count = len(provider.messages())
        _require(count == sum(len(spec.parts) for spec in candidate.deliveries), "mock message count differs")
        return {"candidate_id": candidate.candidate_id, "route_snapshot_id": routes.snapshot_id, "message_count": count,
            "delivery_count": len(ids), "is_replay_idempotent": True}


def _raid(directory, inputs, generated, health_result):
    ranks = {(score["member_id"], score["key"]): score["rank"] for score in generated["scores"]["groups"]}
    members = tuple(RaidMember(member, "=同名", 200-index*50) for index, member in enumerate(MEMBERS))
    problem = RaidProblem(members, (("0",)*5,)*3, ("電擊",)*5, 100, ("10",)*5, (),
        ((MEMBERS[0], FixedInputs(("負一", "", ""))), (MEMBERS[1], FixedInputs(("正五",)*3))),
        tuple((member, tuple((element, ranks.get((member.key, element))) for element in ELEMENTS)) for member in MEMBERS),
        (("healthcheck_result", health_result), ("union_inputs", inputs.snapshot_id)), CODE)
    with closing(LocalRaidPlans(directory / "state.sqlite")) as store:
        revision = store.observe_problem(SCOPE, problem, 0)
        store.submit_job("raid", TaskSpec("generate_raid_draft", SCOPE, CODE, {"problem": problem.problem_id}, family="raid"), is_desired=True)
        computed = run_isolated_worker(directory, CODE, "scenario-raid", seconds=30)
        _require(computed["outcome"] == "completed" and computed["child_result"]["processed"][0]["state"] == "succeeded", "raid worker failed")
        result = store.get_result(store.get_job(computed["child_result"]["processed"][0]["job_id"]).result_id)
        _require(store.confirmed_head(SCOPE) is None, "raid computation auto-confirmed")
        confirmation = store.confirm("raid_confirm", "mock_raid_confirmation", SCOPE, result["draft_plan_id"], "mock_maintainer", revision, None)
        workbook = store.confirmation_workbook(confirmation)
        _require(workbook["workbook_view_id"] == result["workbook_view_id"] and not workbook["is_stale"], "confirmed raid view differs")
        return {"problem_id": problem.problem_id, "draft_plan_id": result["draft_plan_id"], "confirmation_id": confirmation,
            "workbook_view_id": workbook["workbook_view_id"], "source_healthcheck_result_id": health_result}, _execution_receipt(computed)


def _execution_receipt(result):
    _require(result["is_child_stopped"] is True, "compute child termination is unverified")
    processed = result["child_result"]["processed"][0]
    return {"job_id": processed["job_id"], "command": processed["command"], "outcome": result["outcome"],
        "is_child_stopped": result["is_child_stopped"], "exit_code": result["exit_code"],
        "elapsed_seconds": result["seconds"], "configured_deadline_seconds": 30, "memory_limit_bytes": result["memory_limit_bytes"]}


def _retain(directory):
    with closing(LocalObjectRetention(directory / "state.sqlite")) as store:
        orphan = capture_document("union_inputs", SCOPE, _time(0), "mock_unused", {"kind": "unused_synthetic_fixture"})
        store.put_document(orphan)
        plan = store.plan_retention(9999999999)
        _require(orphan.snapshot_id in plan.object_ids, "unreachable fixture was not selected")
        return store.apply_retention(plan, plan.plan_id)


def _standard_rows(threshold):
    return [{"name_code": "role_mock", "display_name": "模擬角色", "elements": ["電擊"], "role_type": "output", "tracking": "追蹤",
        "total_weight": 1, "trait_labels": {"attack": "攻擊"}, "metrics": [
            {"metric_id": "skill_1", "threshold": threshold, "coefficient": 1},
            {"metric_id": "trait_1", "threshold": 1, "coefficient": 1, "function_id": "attack"}]}]


def _pages(reference_version):
    result = {}
    for index, member in enumerate(MEMBERS):
        rows = {"ownership": [{"name_code": "role_mock", "grade": 0, "core": 0}],
            "details": [{"name_code": "role_mock", "skill1_lv": 7-index*4, "equip_option_1": "1000101"}],
            "effects": [{"id": "1000101", "function_details": [{"function_id": "attack", "function_value": 300-index*200}]}]}
        for source, values in rows.items():
            result[(member.key, source, None)] = _page(member, source, values, reference_version)
    return result


def _page(member, source, rows, reference_version):
    return {"member": asdict(member), "scope_id": SCOPE, "source": source, "reference_version": reference_version,
        "source_revision": "mock-stable-revision", "total": len(rows), "next_cursor": None, "rows": rows, "is_options_complete": True}


def _time(minute):
    return f"2026-10-15T00:{minute:02}:00Z"


def _require(condition, message):
    if not condition:
        raise StateConflict(message)


if __name__ == "__main__":
    raise SystemExit(main())
