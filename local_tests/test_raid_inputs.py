from contextlib import closing
from dataclasses import asdict, replace

import pytest

from adapters.local_raid_plans import LocalRaidPlans
from adapters.local_state import LocalStateStore
from adapters.local_system_status import read_system_status
from adapters.local_union_inputs import LocalUnionInputs
from application.raid_inputs import compile_raid_problem
from application.roster_lifecycle import RosterMember, capture_roster_observation, prepare_roster_change
from application.tasks import StateConflict, TaskSpec
from domain.healthcheck import ELEMENTS
from domain.raid import FixedInputs
from domain.raid_finalize import finalize_rounds
from domain.raid_planner import select_rounds
from domain.snapshots import MemberKey, capture_document
from executors.local.demo import run_demo

TIME = '2026-10-15T00:00:00Z'


@pytest.fixture
def compiled(tmp_path):
    directory = tmp_path / 'mock'
    summary = run_demo(directory)
    with closing(LocalRaidPlans(directory / 'state.sqlite')) as store:
        job = store.get_job(summary['job_id'])
        inputs = store.get_document(job.spec.version_map['union_inputs'])
        settings = source('raid_settings', {'hp': [['0']*5]*3, 'boss_elements': list(ELEMENTS), 'base_sync': 100,
            'base_damage_yi': ['10']*5, 'element_tie_order': list(ELEMENTS), 'overflow_policy': 'legacy_effective'})
        humans = source('raid_member_inputs', {'members': [{'member': row['member'], 'sync': 100+index*10,
            'fixed_inputs': asdict(FixedInputs(('負一', '', '')))} for index, row in enumerate(inputs.payload['members'])], 'history': []})
        store.put_document(settings)
        store.put_document(humans)
        problem = compile_raid_problem(store, job.job_id, settings.snapshot_id, humans.snapshot_id, job.spec.code_sha)
        yield directory, store, job, inputs, settings, humans, problem


def source(kind, payload, scope='union_demo', reference='reference_demo'):
    return capture_document('union_inputs', scope, TIME, reference, {'input_kind': kind, 'raid_schema_version': 1, **payload})


def confirm(store, problem):
    revision = store.observe_problem('union_demo', problem, 0)
    final = finalize_rounds(problem, select_rounds(problem))
    store.save_draft('union_demo', problem, final)
    confirmation = store.confirm('confirm', 'intent', 'union_demo', final.draft_plan_id, 'actor', revision, None, True)
    return confirmation, final


def test_compiler_binds_actual_ranks_roster_and_original_human_inputs(compiled):
    directory, store, job, inputs, settings, humans, problem = compiled
    versions = dict(problem.input_versions)
    assert versions['healthcheck_result'] == job.result_id and versions['union_inputs'] == inputs.snapshot_id
    assert versions['raid_settings'] == settings.snapshot_id and versions['raid_member_inputs'] == humans.snapshot_id
    assert [member.member.key for member in problem.members] == [MemberKey(**row['member']).key for row in inputs.payload['members']]
    assert all(fixed.slots == ('負一', '', '') for _, fixed in problem.fixed_inputs)
    scores = next(iter(store.get_result(job.result_id)['workbooks'].values()))['scores']['groups']
    expected = {(row['member_id'], row['key']): row['rank'] for row in scores}
    for member, ranks in problem.ranks:
        assert dict(ranks) == {element: expected.get((member.key, element)) for element in ELEMENTS}
    assert compile_raid_problem(store, job.job_id, settings.snapshot_id, humans.snapshot_id, job.spec.code_sha) == problem
    assert store.input_head('union_demo') is None and store.confirmed_head('union_demo') is None


@pytest.mark.parametrize('change', ['scope', 'reference', 'unknown_field', 'missing_member', 'duplicate_member', 'foreign_member', 'missing_override', 'history_kind', 'history_count'])
def test_compiler_rejects_ambiguous_source_inputs(compiled, change):
    _, store, job, _, settings, humans, _ = compiled
    payload = humans.payload
    scope, reference = 'union_demo', 'reference_demo'
    if change == 'scope': scope = 'another_union'
    elif change == 'reference': reference = 'another_reference'
    elif change == 'unknown_field': payload['cached_rank'] = 1
    elif change == 'missing_member': payload['members'].pop()
    elif change == 'duplicate_member': payload['members'].append(payload['members'][0])
    elif change == 'foreign_member': payload['members'][0]['member']['member_id'] = 'foreign'
    elif change == 'missing_override': del payload['members'][0]['fixed_inputs']['damage_overrides_yi']
    elif change.startswith('history'):
        payload['history'] = [{'knife_id': f'knife_{i}', 'member': payload['members'][0]['member'], 'boss': 1,
            'damage_true': '1', 'kind': 'auto' if change == 'history_kind' else 'history', 'stage': 1, 'is_locked': True} for i in range(4 if change == 'history_count' else 1)]
    bad = capture_document('union_inputs', scope, TIME, reference, payload)
    store.put_document(bad)
    with pytest.raises((StateConflict, ValueError)):
        compile_raid_problem(store, job.job_id, settings.snapshot_id, bad.snapshot_id, job.spec.code_sha)
    assert store.input_head('union_demo') is None


def test_compiler_refuses_unpublished_or_different_release_healthcheck(compiled):
    _, store, job, _, settings, humans, _ = compiled
    with pytest.raises(StateConflict):
        compile_raid_problem(store, job.job_id, settings.snapshot_id, humans.snapshot_id, 'different_code')
    next_job = store.submit_job('not_published', replace(job.spec, intent_id='new_compute'), is_desired=True)
    store.complete_compute(store.claim('worker', job.spec.code_sha), store.get_result(job.result_id))
    with pytest.raises(StateConflict):
        compile_raid_problem(store, next_job.job_id, settings.snapshot_id, humans.snapshot_id, job.spec.code_sha)
    with pytest.raises(StateConflict):
        compile_raid_problem(store, job.job_id, settings.snapshot_id, humans.snapshot_id, job.spec.code_sha)


def test_forged_rank_or_sync_cannot_be_observed_as_compiled_problem(compiled):
    _, store, _, _, _, _, problem = compiled
    bad = replace(problem, members=(replace(problem.members[0], sync=999), *problem.members[1:]))
    with pytest.raises(StateConflict, match='differs'):
        store.observe_problem('union_demo', bad, 0)
    assert store.input_head('union_demo') is None
    assert store.observe_problem('union_demo', problem, 0) == 1


def test_new_healthcheck_desired_makes_existing_confirmation_stale_immediately(compiled):
    directory, store, job, _, _, _, problem = compiled
    confirmation, final = confirm(store, problem)
    original = store.confirmation(confirmation)
    assert not store.confirmation_status(confirmation)['is_stale']
    store.submit_job('new_health', replace(job.spec, intent_id='new_health'), is_desired=True)
    assert store.input_head('union_demo')['problem_id'] == problem.problem_id
    assert store.confirmation_status(confirmation)['is_stale']
    assert store.confirmation(confirmation) == original
    assert read_system_status(directory, ('union_demo',))['scopes'][0]['raid_confirmation']['is_stale']
    with pytest.raises(StateConflict, match='stale'):
        store.confirm('other_request', 'other_intent', 'union_demo', final.draft_plan_id, 'actor', 1, confirmation, True)
    assert store.confirm('confirm', 'intent', 'union_demo', final.draft_plan_id, 'actor', 1, None, True) == confirmation


def test_roster_change_stales_confirmed_plan_without_replacing_problem_or_old_view(compiled):
    directory, store, job, inputs, _, _, problem = compiled
    confirmation, _ = confirm(store, problem)
    original = store.confirmation_workbook(confirmation)
    with closing(LocalUnionInputs(directory / 'state.sqlite')) as registry:
        registry.initialize_union_inputs('init', inputs, inputs.snapshot_id, 'actor', job.spec.code_sha)
        survivor = inputs.payload['members'][1]
        observation = capture_roster_observation('union_demo', TIME, 'reference_demo', (RosterMember(MemberKey(**survivor['member']), survivor['display_name']),), 'complete', 'departed')
        plan = prepare_roster_change(inputs, observation, job.spec.code_sha)
        cid = registry.create_roster_candidate(plan, observation)
        registry.adopt_roster_candidate('adopt', cid, cid, 'actor', job.spec.code_sha)
    current = store.confirmation_workbook(confirmation)
    assert current['is_stale'] and current['workbook_view'] == original['workbook_view']
    assert current['workbook_view_id'] == original['workbook_view_id']
    assert read_system_status(directory, ('union_demo',))['scopes'][0]['raid_confirmation']['is_stale']


def test_managed_scope_rejects_legacy_problem_that_bypasses_compiler(compiled):
    directory, store, job, inputs, _, _, problem = compiled
    with closing(LocalUnionInputs(directory / 'state.sqlite')) as registry:
        registry.initialize_union_inputs('init', inputs, inputs.snapshot_id, 'actor', job.spec.code_sha)
    legacy = replace(problem, input_versions=(('union_inputs', inputs.snapshot_id),))
    with pytest.raises(StateConflict, match='stale'):
        store.observe_problem('union_demo', legacy, 0)
    assert store.observe_problem('union_demo', problem, 0) == 1

@pytest.mark.parametrize('phase', ['queued', 'running', 'computed'])
def test_compiled_raid_job_loses_eligibility_when_healthcheck_desired_changes(compiled, phase):
    from application.raid_inputs import raid_task_spec

    _, store, health, _, _, _, problem = compiled
    store.observe_problem('union_demo', problem, 0)
    job = store.submit_job('raid_job', raid_task_spec('union_demo', problem), is_desired=True)
    lease = store.claim('worker', health.spec.code_sha) if phase != 'queued' else None
    if phase == 'computed':
        store.complete_compute(lease, {'synthetic': True})
    store.submit_job('new_health', replace(health.spec, intent_id='new_source'), is_desired=True)
    assert not store.is_job_current(job.job_id)
    if phase == 'queued':
        # The old raid is skipped even if the next healthcheck is claimed.
        claimed = store.claim('worker', health.spec.code_sha, commands=('generate_raid_draft',))
        assert claimed is None
    elif phase == 'running':
        assert store.complete_compute(lease, {'synthetic': True}, is_publication_required=False).state == 'superseded'
    else:
        assert store.begin_publication(job.job_id, 'publisher', health.spec.code_sha, ('mock_book',)) is None
    assert store.get_job(job.job_id).state == 'superseded'


def test_compiled_raid_task_runs_from_bound_sources_without_auto_confirmation(compiled):
    from application.compute_budget import ComputeBudget
    from application.raid_execution import run_raid_job
    from application.raid_inputs import raid_task_spec

    _, store, health, _, _, _, problem = compiled
    store.observe_problem('union_demo', problem, 0)
    job = store.submit_job('raid_job', raid_task_spec('union_demo', problem), is_desired=True)
    result = run_raid_job(store, store.claim('worker', health.spec.code_sha), problem, ComputeBudget())
    assert result.job_id == job.job_id and result.state == 'succeeded'
    assert store.get_result(result.result_id)['problem_id'] == problem.problem_id
    assert store.confirmed_head('union_demo') is None


def test_corrupt_compiled_confirmation_is_isolated_in_read_only_status(compiled):
    directory, store, _, _, _, _, problem = compiled
    confirmation, _ = confirm(store, problem)
    store._connection.execute('UPDATE objects SET serialized=? WHERE object_id=?', ('{}', problem.problem_id))
    status = read_system_status(directory, ('union_demo', 'another_union'))
    assert not status['scopes'][0]['is_available'] and status['scopes'][1]['is_available']
    assert status['scopes'][0]['error_code'] == 'status_input_invalid'
