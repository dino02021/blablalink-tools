from dataclasses import asdict, replace
import json

import pytest

from adapters.mock_grid import materialize_generated_grid
from adapters.mock_sheets import MockSheets
from application.publication import publish_workbooks
from application.raid_publication import compile_raid_publication, run_raid_publication_job
from application.tasks import ResourceBusy, StateConflict
from domain.snapshots import capture_document
from executors.local.worker import run_worker
from local_tests.test_raid_inputs import TIME, compiled, confirm


@pytest.fixture
def prepared(compiled):
    directory, store, health, inputs, settings, humans, problem = compiled
    confirmation, final = confirm(store, problem)
    route = capture_document('union_inputs', 'union_demo', TIME, 'reference_demo',
        {'input_kind': 'raid_publication_route', 'resource': 'raid_output', 'layout_revision': 'raid_layout_v1'})
    store.put_document(route)
    candidate = compile_raid_publication(store, confirmation, route.snapshot_id, health.spec.code_sha)
    sheets = MockSheets(directory / 'workbooks')
    sheets.create('raid_output', 'raid_layout_v1', {'human_fixed_slots': ['負一', '', '']})
    sheets.set_manual_tab('raid_output', 'notes', '人工備註', [['=literal', 0, '']])
    yield directory, store, health, problem, confirmation, final, route, candidate, sheets


def enqueue(store, candidate, request='publish', intent='explicit_publish'):
    return store.prepare_raid_publication(request, candidate, candidate.candidate_id, intent)


def compute(store, health, candidate):
    job = enqueue(store, candidate)
    completed = run_raid_publication_job(store, store.claim('worker', health.spec.code_sha))
    assert completed.job_id == job.job_id and completed.state == 'computed'
    return completed


def test_confirmed_publication_is_explicit_and_compute_only_preserves_workbook(prepared):
    directory, store, health, problem, confirmation, _, route, candidate, sheets = prepared
    before = sheets.read('raid_output')
    job = enqueue(store, candidate)
    assert store.get_result(candidate.candidate_id) == json.loads(json.dumps(asdict(candidate)))
    result = run_worker(directory, health.spec.code_sha, max_jobs=1)
    assert result['processed'][0]['job_id'] == job.job_id and result['processed'][0]['state'] == 'computed'
    assert sheets.read('raid_output') == before
    assert store.confirmed_head('union_demo') == confirmation
    result = run_worker(directory, health.spec.code_sha, max_jobs=1, is_publish_enabled=True)
    assert result['processed'][0]['state'] == 'succeeded'
    book = sheets.read('raid_output')
    assert book['generated']['confirmation_id'] == confirmation
    assert book['generated']['problem_id'] == problem.problem_id
    assert len(book['generated_grid']['tabs']) == 5
    assert all(tab['is_generated_protected'] for tab in book['generated_grid']['tabs'].values())
    assert book['inputs'] == before['inputs'] and book['manual_tabs'] == before['manual_tabs']
    assert book['generated']['raid_workbook_view'] == json.loads(candidate.manifest_json)['workbooks']['raid_output']['raid_workbook_view']
    assert enqueue(store, candidate).job_id == job.job_id
    assert run_worker(directory, health.spec.code_sha, is_publish_enabled=True)['processed'] == []


@pytest.mark.parametrize('change', ['confirmation', 'manifest', 'code', 'route'])
def test_publication_candidate_requires_fixed_confirmation_and_sources(prepared, change):
    _, store, _, _, _, _, _, candidate, sheets = prepared
    before = sheets.read('raid_output')
    changed = candidate
    if change == 'manifest': changed = replace(candidate, manifest_json='{}')
    elif change == 'code': changed = replace(candidate, code_sha='other')
    elif change == 'route': changed = replace(candidate, route_id=candidate.problem_id)
    with pytest.raises((StateConflict, ValueError, KeyError)):
        store.prepare_raid_publication('wrong', changed, 'wrong' if change == 'confirmation' else changed.candidate_id, 'intent')
    assert sheets.read('raid_output') == before
    assert not any(job.spec.command == 'publish_raid_plan' for job in store.list_jobs())


@pytest.mark.parametrize('change', ['scope', 'reference', 'kind', 'extra'])
def test_route_is_a_fixed_scoped_document(prepared, change):
    _, store, health, _, confirmation, _, route, _, _ = prepared
    payload = route.payload
    if change == 'kind': payload['input_kind'] = 'other'
    elif change == 'extra': payload['user_supplied_target'] = 'no'
    bad = capture_document('union_inputs', 'other' if change == 'scope' else 'union_demo', TIME,
        'other' if change == 'reference' else 'reference_demo', payload)
    store.put_document(bad)
    with pytest.raises(StateConflict):
        compile_raid_publication(store, confirmation, bad.snapshot_id, health.spec.code_sha)


@pytest.mark.parametrize('phase', ['queued', 'running', 'computed'])
def test_changed_healthcheck_blocks_confirmed_publication_at_every_phase(prepared, phase):
    _, store, health, _, _, _, _, candidate, sheets = prepared
    job = enqueue(store, candidate)
    lease = store.claim('worker', health.spec.code_sha) if phase != 'queued' else None
    if phase == 'computed':
        run_raid_publication_job(store, lease)
    store.submit_job('new_health', replace(health.spec, intent_id='new_source'), is_desired=True)
    assert not store.is_job_current(job.job_id)
    before = sheets.read('raid_output')
    if phase == 'queued':
        assert store.claim('worker', health.spec.code_sha, commands=('publish_raid_plan',)) is None
    elif phase == 'running':
        assert store.complete_compute(lease, json.loads(candidate.manifest_json)).state == 'superseded'
    else:
        assert store.begin_publication(job.job_id, 'publisher', health.spec.code_sha, ('raid_output',)) is None
    assert sheets.read('raid_output') == before
    assert enqueue(store, candidate).job_id == job.job_id  # exact replay is a receipt, not new eligibility
    with pytest.raises(StateConflict):
        enqueue(store, candidate, request='new_request')


@pytest.mark.parametrize('fault', ['before', 'after'])
def test_lost_publication_response_keeps_fence_until_positive_recovery(prepared, fault):
    _, store, health, problem, confirmation, final, _, candidate, sheets = prepared
    completed = compute(store, health, candidate)
    manifest = store.get_result(completed.result_id)
    claim = store.begin_publication(completed.job_id, 'publisher', health.spec.code_sha, ('raid_output',))
    with pytest.raises(TimeoutError):
        publish_workbooks(store, sheets, claim, manifest['workbooks'], manifest['layout_revisions'], {'raid_output': fault})
    assert store.get_job(completed.job_id).state == 'publish_uncertain'
    with pytest.raises(ResourceBusy):
        store.confirm('new_confirm', 'new_confirm_intent', 'union_demo', final.draft_plan_id, 'actor', 1, confirmation, True)
    observations = {'raid_output': sheets.observe('raid_output', manifest['workbooks']['raid_output'], claim.commit_id)}
    with pytest.raises(StateConflict):
        store.recover_publication(claim.publication_id, 'reconciler', observations, False)
    resumed = store.recover_publication(claim.publication_id, 'reconciler', observations, True)
    if fault == 'before':
        assert resumed is not None
        publish_workbooks(store, sheets, resumed, manifest['workbooks'], manifest['layout_revisions'])
    else:
        assert resumed is None
    assert store.get_job(completed.job_id).state == 'succeeded'
    assert sheets.write_count == 1 and sheets.read('raid_output')['generated']['confirmation_id'] == confirmation


def test_new_confirmation_stales_older_publication_without_changing_old_candidate(prepared):
    _, store, health, problem, confirmation, final, route, candidate, sheets = prepared
    job = compute(store, health, candidate)
    new_confirmation = store.confirm('new_confirm', 'new_intent', 'union_demo', final.draft_plan_id, 'actor', 1, confirmation, True)
    assert new_confirmation != confirmation
    assert store.begin_publication(job.job_id, 'publisher', health.spec.code_sha, ('raid_output',)) is None
    with pytest.raises(StateConflict):
        compile_raid_publication(store, confirmation, route.snapshot_id, health.spec.code_sha)
    assert sheets.read('raid_output')['generated'] == {}


def test_raid_grid_binding_rejects_relabeling_and_ambiguous_view(prepared):
    _, _, _, _, _, _, _, candidate, _ = prepared
    generated = json.loads(candidate.manifest_json)['workbooks']['raid_output']
    assert materialize_generated_grid(generated)['view_id'] == generated['view_id']
    for key in ('problem_id', 'draft_plan_id', 'code_sha', 'view_id'):
        with pytest.raises(ValueError):
            materialize_generated_grid({**generated, key: 'wrong'})
    with pytest.raises(ValueError):
        materialize_generated_grid({**generated, 'view': {}})
    with pytest.raises(ValueError):
        materialize_generated_grid({key: value for key, value in generated.items() if key != 'publication_kind'})

def test_publication_manifest_obeys_cooperative_budget_without_writing(prepared):
    from application.compute_budget import ComputeBudget

    _, store, health, _, _, _, _, candidate, sheets = prepared
    job = enqueue(store, candidate)
    before = sheets.read('raid_output')
    result = run_raid_publication_job(store, store.claim('worker', health.spec.code_sha), ComputeBudget(max_checks=1))
    assert result.job_id == job.job_id and result.state == 'capacity_blocked'
    assert result.error_code == 'compute_operation_limit' and result.result_id is None
    assert sheets.read('raid_output') == before


def test_native_compute_child_prepares_but_does_not_publish_confirmed_raid(prepared):
    from executors.local.isolated import run_isolated_worker

    directory, store, health, _, _, _, _, candidate, sheets = prepared
    job = enqueue(store, candidate)
    before = sheets.read('raid_output')
    receipt = run_isolated_worker(directory, health.spec.code_sha, 'isolated_raid_publication', seconds=30)
    assert receipt['outcome'] == 'completed'
    assert receipt['child_result']['processed'][0]['job_id'] == job.job_id
    assert store.get_job(job.job_id).state == 'computed'
    assert sheets.read('raid_output') == before

def test_status_retains_latest_success_for_health_and_manual_raid_families(prepared):
    from adapters.local_system_status import read_system_status
    from application.system_status_view import render_system_status

    directory, store, health, _, _, _, _, candidate, _ = prepared
    job = enqueue(store, candidate)
    run_worker(directory, health.spec.code_sha, is_publish_enabled=True)
    status = read_system_status(directory, ('union_demo',))
    scope = status['scopes'][0]
    latest = {item['family']: item for item in scope['published_by_family']}
    assert latest['healthcheck']['job_id'] == health.job_id
    assert latest['raid_publication']['job_id'] == job.job_id
    assert all(item['is_current_target'] for item in latest.values())
    view = render_system_status(status)
    assert len(view.rows) == 3
    assert {row[1].value for row in view.rows[1:]} == {'healthcheck', 'raid_publication'}
    assert all(row[6].value for row in view.rows[1:])
