from contextlib import closing
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path

import pytest

from adapters.local_state import LocalStateStore
from adapters.mock_collection_script import MockScriptCollection, capture_mock_collection_script
from application.collection import collect_training
from application.supplementary_collection import prepare_supplementary_inputs, supplementary_task_spec
from application.tasks import TaskSpec
from domain.mock_workspace import MOCK_WORKSPACE_MARKER
from domain.snapshots import MemberKey, canonical_json, capture_document
from executors.local.isolated import run_isolated_worker
from executors.local.worker import run_worker
from local_tests.test_supplementary_collection import A, B, TIME, FRESH, source, pages


def bind(source, is_bound=True, wrong_scope=False):
    store, config, raw, reference, inputs, _ = source
    prepared = prepare_supplementary_inputs(config,raw,reference,inputs,'code',TIME)
    store.put_document(prepared)
    sources = script_sources(pages(reference))
    script = capture_mock_collection_script('other' if wrong_scope else 'union_demo',TIME,reference.envelope['reference_version'],sources)
    store.put_document(script)
    spec = supplementary_task_spec(prepared,'worker_supplement',script.snapshot_id if is_bound else None)
    job = store.submit_job('worker_supplement',spec)
    directory = Path(store._connection.execute('PRAGMA database_list').fetchone()[2]).parent
    (directory/'mock-workspace.json').write_text(canonical_json(MOCK_WORKSPACE_MARKER),encoding='utf-8')
    return directory, job, script


def script_sources(port):
    return [{'member':response['member'],'source':response['source'],'outcome':'pages',
        'pages':[{key:response[key] for key in ('rows','total','is_options_complete')}]} for response in port.pages.values()]


def test_mock_worker_runs_bound_supplement_without_any_publish(source):
    directory, job, script = bind(source)
    result = run_worker(directory,'code',is_publish_enabled=True,max_jobs=1)
    assert result['processed'][0]['job_id'] == job.job_id and result['processed'][0]['state'] == 'succeeded'
    assert result['network_providers'] == []
    store = source[0]
    output = store.get_result(store.get_job(job.job_id).result_id)
    assert output['is_training_ready']
    assert list((directory/'workbooks').iterdir()) == []
    assert store._connection.execute('SELECT COUNT(*) FROM publications').fetchone()[0] == 0
    assert run_worker(directory,'code')['processed'] == []


def test_unbound_collection_is_not_claimed_or_failed_by_mock_worker(source):
    directory, job, _ = bind(source,is_bound=False)
    assert run_worker(directory,'code')['processed'] == []
    state = source[0].get_job(job.job_id)
    assert state.state == 'queued' and state.attempt == 0


def test_native_worker_can_collect_bound_mock_script_without_python_parent_port(source):
    directory, job, _ = bind(source)
    result = run_isolated_worker(directory,'code','mock_collector_child',seconds=30)
    assert result['outcome'] == 'completed' and result['child_result']['processed'][0]['job_id'] == job.job_id
    current = source[0].get_job(job.job_id)
    assert current.state == 'succeeded' and source[0].get_result(current.result_id)['is_training_ready']
    assert not (directory/'workbooks').exists()


def test_wrong_scope_script_is_rejected_before_collection_observation(source):
    directory, job, _ = bind(source,wrong_scope=True)
    result = run_worker(directory,'code')
    assert result['processed'][0]['state'] == 'failed' and result['processed'][0]['error_code'] == 'worker_input_invalid'
    assert source[0].get_job(job.job_id).result_id is None
    assert source[0]._connection.execute('SELECT COUNT(*) FROM collection_observations').fetchone()[0] == 0


def test_bound_regular_collection_uses_same_mock_transport(source):
    store, _, _, reference, _, _ = source
    port = pages(reference,(A,B))
    script = capture_mock_collection_script('union_demo',TIME,reference.envelope['reference_version'],script_sources(port))
    inputs = capture_document('union_inputs','union_demo',TIME,reference.envelope['reference_version'],
        {'collection_kind':'training_inputs','members':[asdict(A),asdict(B)],'role_ids':['role_demo'],'function_prefixes':{}})
    for document in (script,inputs): store.put_document(document)
    spec = TaskSpec('refresh_roster_and_training','union_demo','code',{'collection_inputs':inputs.snapshot_id,
        'mock_collection_script':script.snapshot_id},family='collection',intent_id='regular_mock_collection')
    job = store.submit_job('regular',spec)
    directory = Path(store._connection.execute('PRAGMA database_list').fetchone()[2]).parent
    (directory/'mock-workspace.json').write_text(canonical_json(MOCK_WORKSPACE_MARKER),encoding='utf-8')
    result = run_worker(directory,'code',max_jobs=1)
    assert result['processed'][0]['job_id'] == job.job_id and result['processed'][0]['state'] == 'succeeded'
    output = store.get_result(store.get_job(job.job_id).result_id)
    assert len(store.get_document(output['raw_snapshot_id']).payload['members']) == 2


def test_capability_filter_runs_before_queue_limit_and_keeps_unbound_attempts_zero(tmp_path):
    with closing(LocalStateStore(tmp_path/'state.sqlite',clock=lambda:100)) as store:
        old = [store.submit_job(f'unbound_{i}',TaskSpec('refresh_roster_and_training','union','code',intent_id=f'unbound_{i}')) for i in range(110)]
        bound = store.submit_job('bound',TaskSpec('refresh_roster_and_training','union','code',{'mock_collection_script':'fixed_mock_script'},intent_id='bound'))
        lease = store.claim('worker','code',commands=('refresh_roster_and_training',),
            required_versions={'refresh_roster_and_training':('mock_collection_script',)})
        assert lease.job_id == bound.job_id
        assert all(store.get_job(job.job_id).state=='queued' and store.get_job(job.job_id).attempt==0 for job in old)


@pytest.mark.parametrize('requirements', [False, {'wrong_command':('key',)}, {'reconcile':()}, {'reconcile':('duplicate','duplicate')}, {'reconcile':("bad'key",)}, {'reconcile':([{}],)}])
def test_invalid_worker_capabilities_fail_before_mutating_jobs(tmp_path, requirements):
    with closing(LocalStateStore(tmp_path/'state.sqlite')) as store:
        with pytest.raises(ValueError): store.claim('worker','code',required_versions=requirements)
        assert store.list_jobs() == ()


def test_script_models_pagination_without_storing_provider_cursors_or_revision_tokens():
    member = A
    sources = []
    for kind, rows in (('ownership',[{'name_code':'role_a'},{'name_code':'role_b'}]),
            ('details',[{'name_code':'role_a','skill1_lv':7},{'name_code':'role_b','skill1_lv':3}]),('effects',[])):
        pages = [{'rows':[row],'total':len(rows),'is_options_complete':True} for row in rows] or [{'rows':[],'total':0,'is_options_complete':True}]
        sources.append({'member':asdict(member),'source':kind,'outcome':'pages','pages':pages})
    script = capture_mock_collection_script('union',TIME,'reference',sources)
    inputs = capture_document('union_inputs','union',TIME,'reference',{'members':[asdict(member)]})
    port = MockScriptCollection(script,inputs)
    raw = collect_training(port,'union',FRESH,'reference',(member,),('role_a','role_b'))
    assert len(port.calls) == 5 and len(raw.payload['members'][0]['ownership']['characters']) == 2
    assert 'cursor' not in script.serialized and 'revision' not in script.serialized
    assert port.is_stable_snapshot_revision
    assert all(source['status']=='complete' for source in raw.payload['collection_proof'][0]['sources'].values())


@pytest.mark.parametrize('change', ['unknown_field','secret_row','outcome','duplicates'])
def test_mock_script_refuses_unbounded_or_nonwhitelisted_source_shapes(source, change):
    reference = source[3]
    sources = script_sources(pages(reference))
    if change=='unknown_field': sources[0]['provider_url']='https://never-connect.invalid'
    elif change=='secret_row': sources[0]['pages'][0]['rows'][0]['authorization']='must-not-persist'
    elif change=='outcome': sources[0]['outcome']='provider_callback'
    else: sources.append(deepcopy(sources[0]))
    with pytest.raises(ValueError): capture_mock_collection_script('union_demo',TIME,reference.envelope['reference_version'],sources)
