from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace

import pytest

from adapters.local_raid_sources import LocalRaidSources
from adapters.local_retention import LocalObjectRetention
from adapters.local_system_status import read_system_status
from application.raid_inputs import compile_raid_problem, raid_task_spec
from application.raid_publication import compile_raid_publication, run_raid_publication_job
from application.tasks import ResourceBusy, StateConflict
from domain.snapshots import capture_document
from local_tests.test_raid_publication import prepared, enqueue
from local_tests.test_raid_inputs import TIME, compiled


@pytest.fixture
def registry(prepared):
    directory, _, health, problem, confirmation, final, route, publication, sheets = prepared
    with closing(LocalRaidSources(directory / 'state.sqlite')) as store:
        versions = dict(problem.input_versions)
        candidate = store.create_raid_source_candidate(health.job_id, versions['raid_settings'], versions['raid_member_inputs'], route.snapshot_id, TIME, health.spec.code_sha)
        yield directory, store, health, problem, confirmation, final, route, publication, sheets, candidate


def adopt(store, candidate, request='adopt'):
    return store.adopt_raid_sources(request, candidate, candidate, 'actor', 'mock-release-v1')


def changed_candidate(store, health, problem, route, change):
    versions = dict(problem.input_versions)
    settings, humans, output = (store.get_document(versions['raid_settings']), store.get_document(versions['raid_member_inputs']), route)
    source = settings if change == 'settings' else humans if change == 'humans' else route
    payload = source.payload
    if change == 'settings': payload['base_damage_yi'][0] = '99'
    elif change == 'humans': payload['members'][0]['fixed_inputs']['slots'][0] = '正五'
    else: payload['resource'] = 'another_raid_book'
    changed = capture_document('union_inputs', 'union_demo', TIME, 'reference_demo', payload)
    store.put_document(changed)
    if change == 'settings': settings = changed
    elif change == 'humans': humans = changed
    else: output = changed
    return store.create_raid_source_candidate(health.job_id, settings.snapshot_id, humans.snapshot_id, output.snapshot_id, TIME, health.spec.code_sha), changed


def test_source_preview_does_not_adopt_and_old_receipt_never_rewinds_head(registry):
    _, store, health, problem, confirmation, _, route, _, _, candidate = registry
    assert store.raid_source_version('union_demo') is None
    assert not store.confirmation_status(confirmation)['is_stale']
    first = adopt(store, candidate)
    assert first['revision'] == 1
    changed, _ = changed_candidate(store, health, problem, route, 'settings')
    second = adopt(store, changed, 'change')
    assert second['revision'] == 2
    assert adopt(store, candidate, 'replay') == first
    assert store.raid_source_version('union_demo')['bundle_id'] == changed
    assert store.input_head('union_demo')['problem_id'] == problem.problem_id


@pytest.mark.parametrize('change', ['settings', 'humans'])
def test_settings_or_fixed_inputs_adoption_stales_old_confirmed_plan_immediately(registry, change):
    directory, store, health, problem, confirmation, _, route, _, _, candidate = registry
    adopt(store, candidate)
    original = store.confirmation_workbook(confirmation)
    next_candidate, changed = changed_candidate(store, health, problem, route, change)
    assert not store.confirmation_status(confirmation)['is_stale']  # preview is not adoption
    adopt(store, next_candidate, 'change')
    status = store.confirmation_status(confirmation)
    assert status['is_stale'] and store.input_head('union_demo')['problem_id'] == problem.problem_id
    assert store.confirmation_workbook(confirmation)['workbook_view'] == original['workbook_view']
    head = store.raid_source_version('union_demo')
    next_problem = compile_raid_problem(store, health.job_id, head['settings_id'], head['member_inputs_id'], health.spec.code_sha)
    assert next_problem.problem_id != problem.problem_id
    assert store.observe_problem('union_demo', next_problem, 1) == 2
    observed = read_system_status(directory, ('union_demo',))['scopes'][0]
    assert observed['raid_source_head']['bundle_id'] == next_candidate and observed['raid_confirmation']['is_stale']


def test_route_only_adoption_keeps_plan_current_but_rejects_old_publication(registry):
    _, store, health, problem, confirmation, _, route, publication, sheets, candidate = registry
    adopt(store, candidate)
    job = enqueue(store, publication)
    run_raid_publication_job(store, store.claim('worker', health.spec.code_sha))
    changed, new_route = changed_candidate(store, health, problem, route, 'route')
    adopt(store, changed, 'route_change')
    assert not store.confirmation_status(confirmation)['is_stale']
    assert not store.is_job_current(job.job_id)
    assert store.begin_publication(job.job_id, 'publisher', health.spec.code_sha, ('raid_output',)) is None
    assert sheets.read('raid_output')['generated'] == {}
    with pytest.raises(StateConflict, match='route'):
        compile_raid_publication(store, confirmation, route.snapshot_id, health.spec.code_sha)
    assert compile_raid_publication(store, confirmation, new_route.snapshot_id, health.spec.code_sha).route_id == new_route.snapshot_id


def test_candidates_from_same_base_cannot_both_be_adopted(registry):
    directory, store, health, problem, _, _, route, _, _, initial = registry
    adopt(store, initial)
    first, _ = changed_candidate(store, health, problem, route, 'settings')
    second, _ = changed_candidate(store, health, problem, route, 'humans')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(adopt_in_connection, [(directory / 'state.sqlite', first), (directory / 'state.sqlite', second)]))
    assert sorted(results) == ['adopted', 'stale']
    assert store.raid_source_version('union_demo')['revision'] == 2


def test_adoption_revalidates_health_source_and_does_not_create_head_on_failure(registry):
    _, store, health, _, _, _, _, _, _, candidate = registry
    store.submit_job('new_health', replace(health.spec, intent_id='changed_health'), is_desired=True)
    with pytest.raises(StateConflict):
        adopt(store, candidate)
    assert store.raid_source_version('union_demo') is None


@pytest.mark.parametrize('change', ['confirmation', 'code', 'request'])
def test_exact_confirmation_code_and_request_are_required(registry, change):
    _, store, health, problem, _, _, route, _, _, candidate = registry
    if change == 'request':
        other, _ = changed_candidate(store, health, problem, route, 'settings')
        adopt(store, other)
    with pytest.raises(StateConflict):
        store.adopt_raid_sources('adopt', candidate, 'wrong' if change == 'confirmation' else candidate,
            'actor', 'other' if change == 'code' else health.spec.code_sha)


def test_unknown_publisher_blocks_whole_source_bundle_adoption(registry):
    _, store, health, problem, _, _, route, publication, _, candidate = registry
    adopt(store, candidate)
    job = enqueue(store, publication)
    run_raid_publication_job(store, store.claim('worker', health.spec.code_sha))
    claim = store.begin_publication(job.job_id, 'publisher', health.spec.code_sha, ('raid_output',))
    store.mark_publication_uncertain(claim)
    change, _ = changed_candidate(store, health, problem, route, 'route')
    with pytest.raises(ResourceBusy):
        adopt(store, change, 'change')
    assert store.raid_source_version('union_demo')['bundle_id'] == candidate


def test_head_receipt_and_request_rollback_together(registry):
    _, store, _, _, _, _, _, _, _, candidate = registry
    store._connection.execute("CREATE TRIGGER fail_source_receipt BEFORE INSERT ON raid_source_adoptions BEGIN SELECT RAISE(ABORT,'synthetic'); END")
    with pytest.raises(Exception, match='synthetic'):
        adopt(store, candidate)
    assert store.raid_source_version('union_demo') is None
    assert store._connection.execute('SELECT COUNT(*) FROM raid_source_requests').fetchone()[0] == 0
    store._connection.execute('DROP TRIGGER fail_source_receipt')
    assert adopt(store, candidate)['revision'] == 1


def test_old_legacy_problem_cannot_bypass_adopted_source_head(registry):
    _, store, _, problem, _, _, _, _, _, candidate = registry
    adopt(store, candidate)
    legacy = replace(problem, input_versions=(('legacy', 'old'),))
    with pytest.raises(StateConflict, match='stale'):
        store.observe_problem('union_demo', legacy, 1)
    assert store.observe_problem('union_demo', problem, 1) == 1


def test_restart_and_retention_keep_source_bundle_lineage(registry):
    directory, store, _, _, _, _, _, _, _, candidate = registry
    receipt = adopt(store, candidate)
    with closing(LocalRaidSources(directory / 'state.sqlite')) as restarted:
        assert restarted.raid_source_version('union_demo')['bundle_id'] == candidate
        assert adopt(restarted, candidate) == receipt
    with closing(LocalObjectRetention(directory / 'state.sqlite')) as retention:
        plan = retention.plan_retention(999999999999)
        assert candidate not in plan.object_ids
        data = store.raid_source_candidate(candidate).payload
        assert not ({data[key] for key in ('settings_id','member_inputs_id','route_id','problem_id')} & set(plan.object_ids))


def adopt_in_connection(args):
    path, candidate = args
    with closing(LocalRaidSources(path)) as store:
        try:
            adopt(store, candidate, candidate)
            return 'adopted'
        except StateConflict:
            return 'stale'

@pytest.mark.parametrize('phase', ['queued', 'running', 'computed'])
def test_adopted_settings_change_fences_existing_draft_jobs(registry, phase):
    _, store, health, problem, _, _, route, _, _, candidate = registry
    adopt(store, candidate)
    job = store.submit_job('draft_job', raid_task_spec('union_demo', problem), is_desired=True)
    lease = store.claim('worker', health.spec.code_sha) if phase != 'queued' else None
    if phase == 'computed': store.complete_compute(lease, {'synthetic': True})
    changed, _ = changed_candidate(store, health, problem, route, 'settings')
    adopt(store, changed, 'change')
    assert not store.is_job_current(job.job_id)
    if phase == 'queued':
        assert store.claim('worker', health.spec.code_sha, commands=('generate_raid_draft',)) is None
    elif phase == 'running':
        assert store.complete_compute(lease, {'synthetic': True}, is_publication_required=False).state == 'superseded'
    else:
        assert store.begin_publication(job.job_id, 'publisher', health.spec.code_sha, ('raid_output',)) is None
    assert store.get_job(job.job_id).state == 'superseded'
