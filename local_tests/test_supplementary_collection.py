from contextlib import closing
from copy import deepcopy
from dataclasses import asdict

import pytest

from adapters.local_retention import LocalObjectRetention
from adapters.local_union_inputs import LocalUnionInputs
from adapters.mock_collection import MockCollection
from application.collection import CollectionSourceError
from application.compute_budget import ComputeBudget
from application.reference_catalog import capture_reference
from application.roster_lifecycle import RosterMember, capture_roster_observation, prepare_roster_change
from application.supplementary_collection import merge_supplementary_raw, prepare_supplementary_inputs, run_supplementary_collection_job, supplementary_task_spec
from application.tasks import StateConflict
from domain.healthcheck import MetricRule, RoleRule
from domain.normalization import capture_raw_training, normalize_training
from domain.snapshots import MemberKey, capture_config, capture_document, read_training
from executors.local.demo import raw_member

TIME = '2026-10-15T00:00:00Z'
FRESH = '2026-10-15T00:10:00Z'
A, B = (MemberKey('area_demo', member) for member in ('member_a', 'member_b'))


@pytest.fixture
def source(tmp_path):
    reference = capture_reference('common', TIME, ('role_demo',), {})
    version = reference.envelope['reference_version']
    config = capture_config('common', TIME, version, (RoleRule('role_demo', ('電擊',), 'output', (MetricRule('skill_1', 5, 1),), 1),))
    missing, known = raw_member('member_a', 7), raw_member('member_b', 3)
    missing['details']['character_details'] = []
    raw = capture_raw_training('union_demo', TIME, version, (missing, known), ('role_demo',))
    inputs = capture_document('union_inputs', 'union_demo', TIME, version,
        {'members': [{'member': asdict(member), 'display_name': '=same'} for member in (A, B)],
            'healthcheck_route': {'resource': 'mock_book', 'layout_revision': 'layout'}})
    clock = [100]
    with closing(LocalUnionInputs(tmp_path / 'state.sqlite', clock=lambda: clock[0])) as store:
        for document in (reference, raw, inputs): store.put_document(document)
        candidate = store.create_candidate(config, None)
        store.publish_candidate(candidate, ())
        store.initialize_union_inputs('init', inputs, inputs.snapshot_id, 'actor', 'code')
        yield store, config, raw, reference, inputs, clock


def prepare(source, **changes):
    store, config, raw, reference, inputs, _ = source
    documents = {'config': config, 'raw': raw, 'reference': reference, 'union_inputs': inputs, **changes}
    for document in documents.values(): store.put_document(document)
    prepared = prepare_supplementary_inputs(**documents, code_sha='code', captured_at=TIME)
    store.put_document(prepared)
    job = store.submit_job('supplement', supplementary_task_spec(prepared, 'gap_attempt'))
    return prepared, job


def pages(reference, members=(A,), skill=9):
    result = {}
    for member in members:
        for source, rows in (('ownership', [{'name_code':'role_demo','grade':0,'core':0}]),
                ('details', [{'name_code':'role_demo','skill1_lv':skill}]), ('effects', [])):
            result[(member.key,source,None)] = {'member':asdict(member),'scope_id':'union_demo','reference_version':reference.envelope['reference_version'],
                'source':source,'source_revision':'opaque_revision','rows':rows,'total':len(rows),'next_cursor':None,'is_options_complete':True}
    return MockCollection(result)


def test_only_gap_member_is_collected_and_known_member_keeps_original_source(source):
    store, _, base, reference, inputs, _ = source
    prepared, job = prepare(source)
    assert prepared.payload['members'] == [asdict(A)]
    port = pages(reference)
    result = run_supplementary_collection_job(store, store.claim('collector','code'), port, FRESH, ComputeBudget())
    assert result.job_id == job.job_id and result.state == 'succeeded'
    output = store.get_result(result.result_id)
    assert output['is_training_ready'] and output['followup_plan']['requests'] == []
    assert len(port.calls) == 3 and {call[0] for call in port.calls} == {A.key}
    merged = store.get_document(output['raw_snapshot_id'])
    assert base.serialized == store.get_document(base.snapshot_id).serialized
    assert merged.envelope['captured_at'] == FRESH
    lineage = {MemberKey(**row['member']):row for row in merged.payload['supplementary_lineage']['members']}
    assert lineage[B]['source_raw_id'] == base.snapshot_id and lineage[B]['source_document_captured_at'] == TIME
    assert lineage[A]['source_raw_id'] == output['fresh_raw_id']
    metrics = {record.member:dict(record.metrics) for record in read_training(store.get_document(output['training_snapshot_id']))}
    assert metrics[A]['skill_1'] == 9 and metrics[B]['skill_1'] == 3
    assert store._connection.execute('SELECT COUNT(*) FROM collection_observations').fetchone()[0] == 1
    assert all(job.spec.command == 'refresh_roster_and_training' for job in store.list_jobs())


def test_auth_failure_never_falls_back_to_old_known_ownership_or_creates_ready_result(source):
    store, _, base, reference, _, _ = source
    prepared, job = prepare(source)
    port = pages(reference)
    port.pages[(A.key,'ownership',None)] = CollectionSourceError(True)
    result = run_supplementary_collection_job(store, store.claim('collector','code'), port, FRESH, ComputeBudget())
    assert result.state == 'auth_required' and result.result_id is None
    assert len(port.calls) == 1
    observation = store._connection.execute('SELECT raw_snapshot_id FROM collection_observations').fetchone()
    fresh = store.get_document(observation[0])
    assert fresh.payload['members'][0]['ownership']['status'] == 'auth_required'
    assert fresh.payload['members'][0]['ownership']['characters'] == []
    assert base.payload['members'][0]['ownership']['characters']


def test_partial_observation_retries_from_fixed_inputs_and_preserves_history(source):
    store, _, _, reference, _, clock = source
    prepared, job = prepare(source)
    incomplete = pages(reference)
    incomplete.pages[(A.key,'details',None)]['rows'] = []
    result = run_supplementary_collection_job(store, store.claim('collector','code'), incomplete, FRESH, ComputeBudget())
    assert result.state == 'retry_wait' and result.result_id is None
    clock[0] = 106
    complete = pages(reference)
    result = run_supplementary_collection_job(store, store.claim('collector','code'), complete, '2026-10-15T00:20:00Z', ComputeBudget())
    assert result.state == 'succeeded' and store.get_result(result.result_id)['is_training_ready']
    assert len(complete.calls) == 3
    assert store._connection.execute('SELECT COUNT(*) FROM collection_observations').fetchone()[0] == 2


@pytest.mark.parametrize('change', ['plan', 'members', 'epoch', 'code'])
def test_changed_collection_inputs_are_rejected_before_provider_calls(source, change):
    store, _, _, reference, _, _ = source
    prepared, _ = prepare(source)
    # A separate fixed task cannot authorize a modified plan by copying its other versions.
    data = prepared.payload
    if change == 'plan': data['plan_id'] = '0'*64
    elif change == 'members': data['members'] = [asdict(B)]
    elif change == 'epoch': data['member_epochs'] = {A.key:'a'*64}
    else: data['code_sha'] = 'other'
    bad = capture_document('union_inputs','union_demo',TIME,prepared.envelope['reference_version'],data)
    store.put_document(bad)
    spec = supplementary_task_spec(bad,'different_attempt')
    job = store.submit_job('bad',spec)
    lease = store.claim('collector',spec.code_sha, commands=('refresh_roster_and_training',))
    if lease.job_id != job.job_id:
        store.defer(lease,'failed','skip_good_for_test')
        lease = store.claim('collector',spec.code_sha)
    port = pages(reference)
    with pytest.raises(StateConflict):
        run_supplementary_collection_job(store,lease,port,FRESH,ComputeBudget())
    assert port.calls == []


def test_ready_data_and_auth_blockers_do_not_create_supplementary_inputs(source):
    store, config, base, reference, inputs, _ = source
    known = capture_raw_training('union_demo',TIME,reference.envelope['reference_version'],(raw_member('member_a',7),raw_member('member_b',3)),('role_demo',))
    with pytest.raises(StateConflict): prepare_supplementary_inputs(config,known,reference,inputs,'code',TIME)
    payload = deepcopy(base.payload['members'])
    payload[0]['ownership']['status'] = 'auth_required'
    payload[0]['ownership']['characters'] = []
    blocked = capture_raw_training('union_demo',TIME,reference.envelope['reference_version'],tuple(payload),('role_demo',))
    with pytest.raises(StateConflict): prepare_supplementary_inputs(config,blocked,reference,inputs,'code',TIME)


def test_rejoined_member_gets_fresh_epoch_without_relabeling_other_members(source):
    store, _, base, reference, original, _ = source
    inputs = rejoin_inputs(store, original)
    prepared, job = prepare(source,union_inputs=inputs)
    result = run_supplementary_collection_job(store,store.claim('collector','code'),pages(reference),FRESH,ComputeBudget())
    assert result.state == 'succeeded'
    output = store.get_result(result.result_id)
    training = store.get_document(output['training_snapshot_id'])
    assert training.payload['member_epochs'] == inputs.payload['member_epochs'] and output['is_training_ready']
    assert 'member_epochs' not in base.payload


def test_complete_observation_can_return_explicit_followup_without_claiming_training_ready(source):
    store, config, base, reference, original, _ = source
    inputs = rejoin_inputs(store, original)
    raw_members = base.payload['members']
    raw_members[1]['details']['character_details'] = []
    raw = capture_raw_training('union_demo',TIME,reference.envelope['reference_version'],tuple(raw_members),('role_demo',))
    prepared, _ = prepare(source,raw=raw,union_inputs=inputs)
    assert prepared.payload['members'] == [asdict(A)]
    result = run_supplementary_collection_job(store,store.claim('collector','code'),pages(reference),FRESH,ComputeBudget())
    output = store.get_result(result.result_id)
    assert result.state == 'succeeded' and not output['is_training_ready']
    assert {MemberKey(**row['member']) for row in output['followup_plan']['requests']} == {B}


def test_cooperative_budget_stops_before_fetch_and_retention_keeps_all_lineage(source):
    store, _, _, reference, _, _ = source
    prepared, job = prepare(source)
    port = pages(reference)
    budget = ComputeBudget(memory_limit_bytes=1,memory_reader=lambda:1)
    result = run_supplementary_collection_job(store,store.claim('collector','code'),port,FRESH,budget)
    assert result.state == 'capacity_blocked' and port.calls == []
    store.reroute(job.job_id,'local','code')
    result = run_supplementary_collection_job(store,store.claim('collector','code'),pages(reference),FRESH,ComputeBudget())
    output = store.get_result(result.result_id)
    database = store._connection.execute('PRAGMA database_list').fetchone()[2]
    with closing(LocalObjectRetention(database)) as retention:
        plan = retention.plan_retention(999999999999)
        assert not ({output[key] for key in ('raw_snapshot_id','training_snapshot_id','base_raw_id','fresh_raw_id','fresh_training_id')} & set(plan.object_ids))


def rejoin_inputs(store, original):
    current = original
    for label, members in (('leave', (B,)), ('rejoin', (A,B))):
        observation = capture_roster_observation('union_demo', TIME, original.envelope['reference_version'],
            tuple(RosterMember(member, '=same') for member in members), 'complete', label)
        plan = prepare_roster_change(current, observation, 'code')
        candidate = store.create_roster_candidate(plan, observation)
        store.adopt_roster_candidate(label, candidate, candidate, 'actor', 'code')
        current = plan.target_inputs
    return current


def test_fresh_complete_not_owned_replaces_old_known_ownership_instead_of_falling_back(source):
    store, _, base, reference, _, _ = source
    prepare(source)
    port = pages(reference)
    for kind in ('ownership','details'):
        port.pages[(A.key,kind,None)]['rows'] = []
        port.pages[(A.key,kind,None)]['total'] = 0
    result = run_supplementary_collection_job(store,store.claim('collector','code'),port,FRESH,ComputeBudget())
    output = store.get_result(result.result_id)
    assert output['is_training_ready']
    records = {row.member:row for row in read_training(store.get_document(output['training_snapshot_id']))}
    assert records[A].ownership_status == 'not_owned' and records[B].ownership_status == 'owned'
    assert base.payload['members'][0]['ownership']['characters']


@pytest.mark.parametrize('change', ['scope','reference','members'])
def test_raw_merge_refuses_unrequested_identity_or_reference_relabeling(source, change):
    _, _, base, reference, inputs, _ = source
    fresh = capture_raw_training('other' if change=='scope' else 'union_demo', FRESH,
        'other' if change=='reference' else reference.envelope['reference_version'],
        (raw_member('member_b' if change=='members' else 'member_a',9),), ('role_demo',))
    with pytest.raises(StateConflict):
        merge_supplementary_raw(base,fresh,inputs,(A,))


def test_stale_membership_is_superseded_before_any_collection_call(source):
    store, _, _, reference, original, _ = source
    _, job = prepare(source)
    lease = store.claim('collector','code')
    rejoin_inputs(store,original)
    port = pages(reference)
    result = run_supplementary_collection_job(store,lease,port,FRESH,ComputeBudget())
    assert result.job_id == job.job_id and result.state == 'superseded' and result.result_id is None
    assert port.calls == []
