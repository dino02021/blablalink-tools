"""Explicit confirmed-raid publication candidates; all writes remain mock adapters."""

from dataclasses import asdict, dataclass
from contextlib import nullcontext
import hashlib
import json

from application.raid_codec import restore_problem
from application.raid_inputs import COMPILER
from application.compute_budget import ComputeExecutionCheck, ComputeLimit
from application.tasks import StateConflict, TaskSpec, validate_identifier
from domain.execution import check_execution, execution_checks
from domain.snapshots import canonical_json


@dataclass(frozen=True)
class RaidPublicationCandidate:
    scope_id: str
    confirmation_id: str
    route_id: str
    problem_id: str
    code_sha: str
    versions: tuple[tuple[str, str], ...]
    manifest_json: str

    @property
    def candidate_id(self):
        return hashlib.sha256(canonical_json(asdict(self)).encode()).hexdigest()


def compile_raid_publication(store, confirmation_id, route_id, code_sha):
    for value in (confirmation_id, route_id, code_sha):
        validate_identifier(value)
    record = store.confirmation(confirmation_id)
    status = store.confirmation_status(confirmation_id)
    if status['is_stale'] or not status['is_current_confirmation']:
        raise StateConflict('only current non-stale raid confirmation can be published')
    draft = store.draft(record['scope_id'], record['draft_id'])
    problem = restore_problem(store.get_result(draft['problem_id']))
    versions = dict(problem.input_versions)
    if versions.get('raid_compiler') != COMPILER or problem.code_sha != code_sha:
        raise StateConflict('raid publication requires same-code compiled sources')
    route = store.get_document(route_id)
    payload = route.payload
    source = store.get_document(versions['union_inputs'])
    if (route.envelope['kind'] != 'union_inputs' or route.envelope['scope_id'] != record['scope_id']
            or route.envelope['reference_version'] != source.envelope['reference_version']
            or set(payload) != {'input_kind', 'resource', 'layout_revision'} or payload['input_kind'] != 'raid_publication_route'):
        raise StateConflict('fixed scoped raid publication route required')
    for value in (payload['resource'], payload['layout_revision']):
        validate_identifier(value)
    workbook = store.confirmation_workbook(confirmation_id)
    generated = {'schema_version': 1, 'publication_kind': 'confirmed_raid', 'confirmation_id': confirmation_id,
        'problem_id': problem.problem_id, 'draft_plan_id': record['draft_id'], 'code_sha': code_sha,
        'config_id': versions['healthcheck_config'], 'snapshot_id': versions['training'],
        'raid_workbook_view': workbook['workbook_view'], 'view_id': workbook['workbook_view_id']}
    manifest = {'workbooks': {payload['resource']: generated}, 'layout_revisions': {payload['resource']: payload['layout_revision']}}
    return RaidPublicationCandidate(record['scope_id'], confirmation_id, route_id, problem.problem_id, code_sha,
        tuple(sorted({**versions, 'problem': problem.problem_id, 'raid_confirmation': confirmation_id, 'raid_route': route_id}.items())), canonical_json(manifest))


def raid_publication_task(candidate, intent_id):
    validate_identifier(intent_id)
    return TaskSpec('publish_raid_plan', candidate.scope_id, candidate.code_sha,
        {**dict(candidate.versions), 'publication_candidate': candidate.candidate_id}, family='raid_publication', intent_id=intent_id)


def run_raid_publication_job(store, lease, budget=None):
    try:
        with execution_checks(ComputeExecutionCheck(store, lease, budget)) if budget is not None else nullcontext():
            check_execution()
            return _run_raid_publication_job(store, lease)
    except ComputeLimit as error:
        return store.defer(lease, 'capacity_blocked', error.code)


def _run_raid_publication_job(store, lease):
    job = store.get_job(lease.job_id)
    if job.spec.command != 'publish_raid_plan':
        raise StateConflict('not a raid publication task')
    versions = job.spec.version_map
    candidate = compile_raid_publication(store, versions['raid_confirmation'], versions['raid_route'], job.spec.code_sha)
    if candidate.candidate_id != versions['publication_candidate'] or raid_publication_task(candidate, job.spec.intent_id) != job.spec:
        raise StateConflict('raid publication candidate or job differs')
    stored = store.get_result(candidate.candidate_id)
    if canonical_json(stored) != canonical_json(asdict(candidate)):
        raise StateConflict('sealed raid publication candidate differs')
    check_execution()
    return store.complete_compute(lease, json.loads(candidate.manifest_json))
