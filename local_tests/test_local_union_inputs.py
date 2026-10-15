from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace

import pytest

from adapters.local_retention import LocalObjectRetention
from adapters.local_state import LocalStateStore
from adapters.local_union_inputs import LocalUnionInputs
from application.roster_lifecycle import prepare_roster_change
from application.tasks import ResourceBusy, StateConflict, TaskSpec
from domain.snapshots import capture_document
from local_tests.test_roster_lifecycle import A, B, C, TIME, inputs, observed


@pytest.fixture
def registry(tmp_path, inputs):
    with closing(LocalUnionInputs(tmp_path / 'state.sqlite')) as store:
        receipt = store.initialize_union_inputs('bootstrap', inputs, inputs.snapshot_id, 'actor', 'code')
        assert receipt['revision'] == 1
        yield store


def candidate(store, inputs, members=(B,), names=None):
    observation = observed(members, names=names)
    plan = prepare_roster_change(inputs, observation, 'code')
    return store.create_roster_candidate(plan, observation), plan


def adopt(store, candidate_id, request='adopt'):
    return store.adopt_roster_candidate(request, candidate_id, candidate_id, 'actor', 'code')


def test_bootstrap_and_adoption_replay_never_rewind_new_head(registry, inputs):
    cid, plan = candidate(registry, inputs)
    receipt = adopt(registry, cid)
    head = registry.union_input_head('union')
    assert head['revision'] == 2 and head['input_id'] == plan.target_inputs.snapshot_id
    assert registry.initialize_union_inputs('bootstrap', inputs, inputs.snapshot_id, 'actor', 'code')['revision'] == 1
    assert adopt(registry, cid, 'same_semantic_intent') == receipt
    assert registry.union_input_head('union') == head
    with pytest.raises(StateConflict):
        registry.initialize_union_inputs('wrong_bootstrap', plan.target_inputs, plan.target_inputs.snapshot_id, 'actor', 'code')
    with pytest.raises(StateConflict):
        registry.adopt_roster_candidate('adopt', cid, cid, 'another_actor', 'code')


def test_two_candidates_from_same_head_only_one_can_be_adopted(registry, inputs):
    first, _ = candidate(registry, inputs, (A,))
    second, _ = candidate(registry, inputs, (B,))
    adopt(registry, first)
    with pytest.raises(StateConflict, match='head changed'):
        adopt(registry, second, 'other')
    assert registry.roster_candidate(second)['base_revision'] == 1


def test_read_only_preview_and_blocked_or_forged_candidates(registry, inputs):
    before = registry._connection.execute('SELECT COUNT(*) FROM objects').fetchone()[0]
    observation = observed((A,), status='partial')
    plan = prepare_roster_change(inputs, observation, 'code')
    with pytest.raises(StateConflict):
        registry.create_roster_candidate(plan, observation)
    observation = observed((B,))
    plan = prepare_roster_change(inputs, observation, 'code')
    with pytest.raises(StateConflict):
        registry.create_roster_candidate(replace(plan, target_inputs=inputs), observation)
    assert registry._connection.execute('SELECT COUNT(*) FROM objects').fetchone()[0] == before
    assert registry.union_input_head('union')['revision'] == 1


@pytest.mark.parametrize('change', ['confirmation', 'code', 'request'])
def test_adoption_requires_exact_confirmation_and_request_identity(registry, inputs, change):
    cid, _ = candidate(registry, inputs)
    if change == 'request':
        other, _ = candidate(registry, inputs, (A,))
        adopt(registry, other)
    with pytest.raises(StateConflict):
        registry.adopt_roster_candidate('adopt', cid, 'wrong' if change == 'confirmation' else cid,
            'actor', 'other' if change == 'code' else 'code')


def test_noop_keeps_exact_head_and_revision(registry, inputs):
    cid, plan = candidate(registry, inputs, (B, A))
    assert plan.is_noop
    receipt = adopt(registry, cid)
    assert receipt['revision'] == 1 and registry.union_input_head('union')['input_id'] == inputs.snapshot_id


def test_restart_rebuilds_candidate_and_keeps_archives(tmp_path, inputs):
    path = tmp_path / 'state.sqlite'
    with closing(LocalUnionInputs(path)) as store:
        store.initialize_union_inputs('init', inputs, inputs.snapshot_id, 'actor', 'code')
        cid, plan = candidate(store, inputs)
    with closing(LocalUnionInputs(path)) as store:
        adopt(store, cid)
        assert store.get_document(store.union_input_head('union')['input_id']) == plan.target_inputs
        assert plan.target_inputs.payload['member_archives'][0]['manual_values'] == 0


def test_head_and_receipt_update_are_atomic_on_database_failure(registry, inputs):
    cid, _ = candidate(registry, inputs)
    registry._connection.execute("CREATE TRIGGER fail_receipt BEFORE INSERT ON union_input_adoptions BEGIN SELECT RAISE(ABORT,'synthetic'); END")
    with pytest.raises(Exception, match='synthetic'):
        adopt(registry, cid)
    assert registry.union_input_head('union')['revision'] == 1
    assert registry._connection.execute("SELECT COUNT(*) FROM union_input_requests WHERE request_id='adopt'").fetchone()[0] == 0
    registry._connection.execute('DROP TRIGGER fail_receipt')
    assert adopt(registry, cid)['revision'] == 2


def test_two_real_database_connections_serialize_competing_adoptions(tmp_path, inputs):
    path = tmp_path / 'state.sqlite'
    with closing(LocalUnionInputs(path)) as store:
        store.initialize_union_inputs('init', inputs, inputs.snapshot_id, 'actor', 'code')
        first, _ = candidate(store, inputs, (A,))
        second, _ = candidate(store, inputs, (B,))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(adopt_in_connection, [(path, first), (path, second)]))
    assert sorted(results) == ['adopted', 'stale']


@pytest.mark.parametrize('phase', ['queued', 'running', 'computed'])
def test_head_change_fences_old_claim_completion_and_publication(registry, inputs, phase):
    spec = TaskSpec('prepare_layout', 'union', 'code', {'union_inputs': inputs.snapshot_id}, family='layout')
    job = registry.submit_job('old', spec, is_desired=True)
    lease = registry.claim('worker', 'code') if phase != 'queued' else None
    if phase == 'computed':
        registry.complete_compute(lease, {'synthetic': True})
    cid, _ = candidate(registry, inputs)
    adopt(registry, cid)
    assert not registry.is_job_current(job.job_id)
    if phase == 'queued':
        assert registry.claim('worker', 'code') is None
    elif phase == 'running':
        assert registry.complete_compute(lease, {'synthetic': True}).state == 'superseded'
    else:
        assert registry.begin_publication(job.job_id, 'publisher', 'code', ('mock_book',)) is None
    assert registry.get_job(job.job_id).state == 'superseded'
    with pytest.raises(StateConflict, match='stale'):
        registry.submit_job('new_request_old_input', spec, is_desired=True)


def test_unresolved_publication_blocks_head_adoption_without_timeout_release(registry, inputs):
    job = registry.submit_job('layout', TaskSpec('prepare_layout', 'union', 'code', {'union_inputs': inputs.snapshot_id}), is_desired=True)
    registry.complete_compute(registry.claim('worker', 'code'), {'synthetic': True})
    claim = registry.begin_publication(job.job_id, 'publisher', 'code', ('mock_book',))
    cid, _ = candidate(registry, inputs)
    with pytest.raises(ResourceBusy):
        adopt(registry, cid)
    registry.mark_publication_uncertain(claim)
    registry._clock = lambda: 999999999999
    with pytest.raises(ResourceBusy):
        adopt(registry, cid)
    assert registry.union_input_head('union')['revision'] == 1


def test_inactive_head_stops_work_but_keeps_last_output_and_can_reactivate(registry, inputs):
    cid, plan = candidate(registry, inputs, ())
    receipt = adopt(registry, cid)
    assert receipt['roster_state'] == 'inactive'
    inactive = plan.target_inputs
    with pytest.raises(StateConflict):
        registry.submit_job('inactive', TaskSpec('prepare_layout', 'union', 'code', {'union_inputs': inactive.snapshot_id}), is_desired=True)
    with pytest.raises(StateConflict):
        registry.submit_job('missing_pin', TaskSpec('generate_raid_draft', 'union', 'code'), is_desired=True)
    cid, plan = candidate(registry, inactive, (B,))
    assert plan.rejoined == (B,)
    receipt = adopt(registry, cid, 'reactivate')
    assert receipt['roster_state'] == 'active' and receipt['revision'] == 3
    assert B.key in plan.target_inputs.payload['member_epochs']
    assert plan.target_inputs.payload['overrides'] == []


def test_candidate_history_and_observation_remain_retention_roots(tmp_path, inputs):
    path = tmp_path / 'state.sqlite'
    with closing(LocalUnionInputs(path)) as store:
        store.initialize_union_inputs('init', inputs, inputs.snapshot_id, 'actor', 'code')
        cid, _ = candidate(store, inputs)
        adopt(store, cid)
        orphan = capture_document('union_inputs', 'other', TIME, 'reference', {'note': 'orphan'})
        store.put_document(orphan)
    with closing(LocalObjectRetention(path)) as store:
        plan = store.plan_retention(999999999999)
        assert plan.object_ids == (orphan.snapshot_id,)
        store.apply_retention(plan, plan.plan_id)
    with closing(LocalUnionInputs(path)) as store:
        assert store.roster_candidate(cid)
        assert adopt(store, cid)['revision'] == 2


def adopt_in_connection(args):
    path, cid = args
    with closing(LocalUnionInputs(path)) as store:
        try:
            adopt(store, cid, cid)
            return 'adopted'
        except StateConflict:
            return 'stale'

def test_status_keeps_last_publication_but_marks_old_roster_not_current(tmp_path):
    from adapters.local_system_status import read_system_status
    from application.system_status_view import render_system_status
    from application.roster_lifecycle import RosterMember, capture_roster_observation
    from domain.snapshots import MemberKey
    from executors.local.demo import run_demo

    directory = tmp_path / 'mock'
    summary = run_demo(directory)
    with closing(LocalUnionInputs(directory / 'state.sqlite')) as store:
        old = store.get_job(summary['job_id'])
        current = store.get_document(old.spec.version_map['union_inputs'])
        store.initialize_union_inputs('init', current, current.snapshot_id, 'actor', old.spec.code_sha)
        observation = capture_roster_observation('union_demo', TIME, 'reference_demo', (), 'complete', 'empty_roster')
        plan = prepare_roster_change(current, observation, old.spec.code_sha)
        cid = store.create_roster_candidate(plan, observation)
        store.adopt_roster_candidate('inactive', cid, cid, 'actor', old.spec.code_sha)
    before = (directory / 'state.sqlite').read_bytes()
    status = read_system_status(directory, ('union_demo',))
    scope = status['scopes'][0]
    assert scope['union_input_head']['roster_state'] == 'inactive'
    assert scope['last_published']['job_id'] == summary['job_id']
    assert not scope['last_published']['is_current_target'] and not scope['is_all_desired_completed']
    assert scope['has_blocked_target'] and not scope['desired'][0]['is_union_input_current']
    view = render_system_status(status)
    assert view.rows[1][view.column_ids.index('roster_state')].value == 'inactive'
    assert view.rows[1][view.column_ids.index('union_input_current')].style == 'unknown'
    assert (directory / 'state.sqlite').read_bytes() == before
