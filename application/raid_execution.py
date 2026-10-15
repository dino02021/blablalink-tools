"""Local checkpointed raid computation. Draft generation never publishes or confirms."""

from dataclasses import asdict

from application.compute_budget import ComputeBudget, ComputeExecutionCheck, ComputeLimit
from application.tasks import StateConflict
from application.raid_workbook import render_raid_workbook
from domain.execution import execution_checks
from domain.raid import RaidKnife, RaidSimulation
from domain.raid_finalize import FinalRaidPlan, finalize_rounds
from domain.raid_planner import RaidProblem, RoundSelection, SourceToken, select_rounds
from domain.raid_view import build_raid_view
from domain.snapshots import MemberKey


RaidExecutionCheck = ComputeExecutionCheck


def run_raid_job(store, lease, problem: RaidProblem, budget: ComputeBudget):
    job = store.get_job(lease.job_id)
    if (job.spec.command != "generate_raid_draft" or job.spec.code_sha != problem.code_sha
            or job.spec.version_map.get("problem") != problem.problem_id):
        raise StateConflict("raid job does not match immutable computation inputs")
    if job.spec.executor_type == "vercel" and (budget.seconds > 240 or budget.memory_limit_bytes is None
            or budget.memory_limit_bytes > 1500000000):
        raise ValueError("Vercel profile requires bounded time and memory evidence")
    checkpoint = store.load_checkpoint(job.job_id)
    if checkpoint is None:
        checkpoint = {"schema_version": 1, "problem_id": problem.problem_id, "code_sha": problem.code_sha, "stage": "start"}
        store.save_checkpoint(lease, checkpoint)
    if (type(checkpoint.get("schema_version")) is not int or checkpoint["schema_version"] != 1
            or checkpoint.get("problem_id") != problem.problem_id or checkpoint.get("code_sha") != problem.code_sha
            or checkpoint.get("stage") not in ("start", "selected", "finalized")):
        raise StateConflict("checkpoint dependency or schema mismatch")
    check = RaidExecutionCheck(store, lease, budget)
    try:
        with execution_checks(check):
            check()
            if checkpoint["stage"] == "start":
                selection = select_rounds(problem)
                checkpoint = {**checkpoint, "stage": "selected", "selection": asdict(selection)}
                store.save_checkpoint(lease, checkpoint)
            else:
                selection = restore_selection(checkpoint["selection"])
            if checkpoint["stage"] == "selected":
                check()
                final = finalize_rounds(problem, selection)
                checkpoint = {**checkpoint, "stage": "finalized", "final": asdict(final)}
                store.save_checkpoint(lease, checkpoint)
            else:
                final = restore_final(checkpoint["final"])
            if final.problem_id != problem.problem_id or final.source_selection_id != selection.selection_id:
                raise StateConflict("final checkpoint does not match its selection")
            check()
            view = build_raid_view(problem, final)
            workbook_view = render_raid_workbook(problem, final)
            check()
            store.save_draft(job.spec.scope_id, problem, final)
            check()
            return store.complete_compute(lease, {"problem_id": problem.problem_id, "draft_plan_id": final.draft_plan_id,
                "final": asdict(final), "view": asdict(view), "workbook_view": asdict(workbook_view),
                "workbook_view_id": workbook_view.view_id}, is_publication_required=False)
    except ComputeLimit as error:
        # Last completed stage was persisted before starting the interrupted stage.
        return store.defer(lease, "capacity_blocked", error.code)


def restore_selection(data: dict) -> RoundSelection:
    if data.get("algorithm_version") != "round-selection-v1":
        raise StateConflict("unsupported selection checkpoint algorithm")
    return RoundSelection(data["problem_id"], tuple(SourceToken(_knife(t["knife"]), t["group_rank"], t["top_order"]) for t in data["source_pool"]),
        tuple(_knife(k) for k in data["selected"]), _simulation(data["simulation"]), tuple(data["unused_source_ids"]),
        tuple(tuple(w) for w in data["warnings"]), data["algorithm_version"])


def restore_final(data: dict) -> FinalRaidPlan:
    if data.get("algorithm_version") != "round-finalization-v1":
        raise StateConflict("unsupported final checkpoint algorithm")
    return FinalRaidPlan(data["problem_id"], data["source_selection_id"], _simulation(data["simulation"]),
        tuple(data["unused_source_ids"]), tuple(tuple(w) for w in data["warnings"]), tuple(data["changes"]), data["algorithm_version"])


def _knife(data):
    return RaidKnife(**{**data, "member": MemberKey(**data["member"])})


def _simulation(data):
    return RaidSimulation(tuple(_knife(k) for k in data["knives"]), tuple(tuple(p) for p in data["effective_true"]),
        tuple(tuple(row) for row in data["remaining_hp"]), tuple(data["deficit_bosses"]), data["phase_three_overflow_true"])
