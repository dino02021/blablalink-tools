"""Bounded offline worker. All effects target an explicitly marked mock workspace."""

from pathlib import Path

from adapters.local_raid_plans import LocalRaidPlans
from adapters.mock_sheets import MockSheets
from adapters.process_memory import local_compute_budget
from adapters.local_workbook_staging import LocalWorkbookStaging
from application.publication import publish_workbooks
from application.raid_codec import restore_problem
from application.raid_execution import run_raid_job
from application.sheet_healthcheck import run_healthcheck_job
from application.tasks import LeaseLost, ResourceBusy, StateConflict, validate_identifier
from application.workbook_staging import StagedSheets, prepare_workbook_stage
from executors.local.demo import workspace_status

SUPPORTED_COMMANDS = ("recompute_healthcheck", "generate_raid_draft")


def run_worker(directory, code_sha: str, worker_id: str = "offline-worker", max_jobs: int = 10,
               is_publish_enabled: bool = False, budget_factory=local_compute_budget) -> dict:
    directory = Path(directory).resolve()
    validate_identifier(code_sha)
    validate_identifier(worker_id)
    if type(max_jobs) is not int or not 1 <= max_jobs <= 100 or type(is_publish_enabled) is not bool:
        raise ValueError("bounded worker invocation required")
    workspace_status(directory)  # refuse unmarked paths before opening a writer adapter
    if (directory / "restore-lock.json").exists():
        raise ValueError("restored workspace is fenced read-only")
    store = LocalRaidPlans(directory / "state.sqlite")
    sheets = MockSheets(directory / "workbooks") if is_publish_enabled else None
    staging = LocalWorkbookStaging(directory / "staging.sqlite") if is_publish_enabled else None
    processed = []
    try:
        store.recover()
        if sheets is not None:
            for job in store.list_jobs():
                if len(processed) >= max_jobs:
                    break
                if (job.state == "computed" and job.spec.command == "recompute_healthcheck"
                        and job.spec.executor_type == "local" and job.spec.code_sha == code_sha):
                    _publish(store, sheets, staging, job, worker_id, code_sha)
                    processed.append(_status(store.get_job(job.job_id)))
        while len(processed) < max_jobs:
            lease = store.claim(worker_id, code_sha, commands=SUPPORTED_COMMANDS)
            if lease is None:
                break
            job = store.get_job(lease.job_id)
            try:
                if job.spec.command == "recompute_healthcheck":
                    computed = run_healthcheck_job(store, lease, budget_factory())
                else:
                    problem_id = job.spec.version_map["problem"]
                    problem = restore_problem(store.get_result(problem_id))
                    if problem.problem_id != problem_id:
                        raise StateConflict("restored raid problem differs")
                    computed = run_raid_job(store, lease, problem, budget_factory())
                if sheets is not None and computed.state == "computed":
                    _publish(store, sheets, staging, computed, worker_id, code_sha)
            except LeaseLost:
                pass  # no stale writer is allowed to change the replacement owner's state
            except (ValueError, KeyError, TypeError, StateConflict):
                current = store.get_job(job.job_id)
                if current.state == "running":
                    try:
                        store.defer(lease, "failed", "worker_input_invalid")
                    except LeaseLost:
                        pass
                else:
                    raise
            processed.append(_status(store.get_job(job.job_id)))
        return {"is_mock": True, "processed": processed, "network_providers": []}
    finally:
        if staging is not None:
            staging.close()
        store.close()


def _publish(store, sheets, staging, job, worker_id, code_sha):
    result = store.get_result(job.result_id)
    try:
        claim = store.begin_publication(job.job_id, worker_id, code_sha, tuple(result["workbooks"]))
    except ResourceBusy:
        return  # a later bounded invocation may retry; never release another publisher
    if claim is not None:
        try:
            stages = {}
            for resource in claim.resources:
                stage, chunks = prepare_workbook_stage(store, sheets, claim, resource)
                staging.begin_stage(stage)
                for index, chunk in enumerate(chunks):
                    staging.put_stage_chunk(stage.stage_id, index, chunk)
                staging.seal_stage(stage.stage_id)
                stages[resource] = stage.stage_id
            staged_sheets = StagedSheets(store, sheets, staging, stages)
            publish_workbooks(store, staged_sheets, claim, result["workbooks"], result["layout_revisions"])
        except (TimeoutError, OSError, StateConflict):
            if store.get_job(job.job_id).state == "publishing":
                store.mark_publication_uncertain(claim)
            if store.get_job(job.job_id).state != "publish_uncertain":
                raise


def _status(job):
    return {"job_id": job.job_id, "command": job.spec.command, "state": job.state, "error_code": job.error_code}
