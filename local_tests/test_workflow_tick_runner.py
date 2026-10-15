from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import json

import pytest

from adapters.local_healthcheck_workflows import LocalHealthcheckWorkflows
from adapters.local_union_inputs import LocalUnionInputs
from adapters.mock_collection_script import capture_mock_collection_script
from adapters.mock_sheets import MockSheets
from application.compute_budget import ComputeBudget
from application.healthcheck_workflow import capture_healthcheck_workflow
from domain.snapshots import capture_document
from executors.local.process_guard import run_python_module
from executors.local.worker import run_worker
from executors.local.workflow_ticks import run_workflow_ticks, tick_workflow_scopes
from local_tests.test_healthcheck_workflows import workflow
from local_tests.test_supplementary_collection import source, TIME


def clone_scope(workflow, scope, code='code'):
    directory,store,policy,original = workflow
    reference = original[3]
    base = store.get_document(policy.payload['base_raw_id'])
    raw = capture_document('raw',scope,TIME,reference.envelope['reference_version'],base.payload)
    script_source = store.get_document(policy.payload['mock_script_id'])
    script = capture_mock_collection_script(scope,TIME,reference.envelope['reference_version'],script_source.payload['sources'])
    data = original[4].payload
    data['healthcheck_route']={'resource':'book_'+scope,'layout_revision':'layout'}
    inputs = capture_document('union_inputs',scope,TIME,reference.envelope['reference_version'],data)
    for document in (raw,script):store.put_document(document)
    with closing(LocalUnionInputs(directory/'state.sqlite')) as registry:
        registry.initialize_union_inputs('init_'+scope,inputs,inputs.snapshot_id,'actor',code)
    next_policy = capture_healthcheck_workflow(scope,TIME,reference.envelope['reference_version'],code,reference.snapshot_id,raw.snapshot_id,script.snapshot_id)
    store.configure_healthcheck_workflow('policy_'+scope,next_policy,next_policy.snapshot_id,0,'actor')
    return next_policy


def test_one_worker_invocation_automatically_collects_and_computes_without_publish(workflow):
    directory,store,_,_ = workflow
    result = run_worker(directory,'code',max_jobs=10)
    assert [job['command'] for job in result['processed']]==['refresh_roster_and_training','recompute_healthcheck']
    assert [job['state'] for job in result['processed']]==['succeeded','computed']
    assert result['workflow_ticks'][0]['status']=='health_computed' and result['workflow_scopes_selected']==3
    assert not (directory/'workbooks').exists()
    assert store._connection.execute('SELECT COUNT(*) FROM publications').fetchone()[0]==0


def test_explicit_mock_publish_completes_whole_workflow_in_same_bounded_invocation(workflow):
    directory,store,_,_ = workflow
    sheets=MockSheets(directory/'workbooks');sheets.create('mock_book','layout',{'manual':0})
    sheets.set_manual_tab('mock_book','notes','人工備註',[['=literal',0]])
    before=sheets.read('mock_book')
    result=run_worker(directory,'code',max_jobs=10,is_publish_enabled=True)
    assert result['workflow_ticks'][0]['status']=='health_published'
    assert len(result['processed'])==2 and result['processed'][-1]['state']=='succeeded'
    assert sheets.read('mock_book')['manual_tabs']==before['manual_tabs']
    assert sheets.read('mock_book')['inputs']==before['inputs']
    assert run_worker(directory,'code',is_publish_enabled=True)['processed']==[]


def test_max_workflow_visits_limits_scheduling_independently_of_job_count(workflow):
    directory,store,_,_ = workflow
    first=run_worker(directory,'code',max_jobs=10,max_workflow_checks=1)
    assert first['workflow_scopes_selected']==1 and len(first['processed'])==1
    assert first['processed'][0]['command']=='refresh_roster_and_training'
    second=run_worker(directory,'code',max_jobs=10,max_workflow_checks=1)
    assert second['workflow_scopes_selected']==1 and len(second['processed'])==1
    assert second['processed'][0]['state']=='computed'
    assert len(store.list_jobs())==2


def test_tick_scope_cursor_is_fair_across_restarts(workflow):
    directory,store,_,_=workflow
    clone_scope(workflow,'union_a');clone_scope(workflow,'union_b')
    results=[]
    for _ in range(4):
        result=run_workflow_ticks(directory,'code',max_scopes=1)
        results.append(result['processed'][0]['scope_id'])
    assert results==['union_a','union_b','union_demo','union_a']
    assert len(store.list_jobs())==3


def test_concurrent_cursor_selection_advances_atomically(workflow):
    directory,store,_,_=workflow
    clone_scope(workflow,'union_a');clone_scope(workflow,'union_b')
    with ThreadPoolExecutor(max_workers=2) as pool:
        selected=list(pool.map(take_in_connection,[directory/'state.sqlite']*2))
    assert {item[0] for item in selected}=={'union_a','union_b'}
    assert store.take_workflow_scopes('code',1)==('union_demo',)


def test_one_invalid_scope_does_not_block_healthy_followups_or_expose_body(workflow):
    directory,store,_,_=workflow
    clone_scope(workflow,'union_a');clone_scope(workflow,'union_b')
    store._connection.execute("UPDATE healthcheck_workflow_state SET state_json=? WHERE scope_id='union_a'",('{"private_body":"must-not-leak"}',))
    result=run_workflow_ticks(directory,'code',max_scopes=3)
    outcomes={item['scope_id']:item for item in result['processed']}
    assert outcomes['union_a']['error_code']=='workflow_input_invalid'
    assert outcomes['union_b']['status']=='collecting' and outcomes['union_demo']['status']=='collecting'
    assert 'must-not-leak' not in json.dumps(result)
    assert len(store.list_jobs())==2


def test_budget_after_selection_advances_cursor_but_does_not_lose_pending_work(workflow):
    directory,store,_,_=workflow
    clone_scope(workflow,'union_a');clone_scope(workflow,'union_b')
    limited=tick_workflow_scopes(store,'code',1,ComputeBudget(max_checks=4))
    assert limited['selected_count']==1 and limited['is_budget_limited'] and limited['processed']==[]
    assert store.list_jobs()==()
    seen=[]
    for _ in range(3):seen.append(run_workflow_ticks(directory,'code',max_scopes=1)['processed'][0]['scope_id'])
    assert seen==['union_b','union_demo','union_a'] and len(store.list_jobs())==3


def test_release_affinity_skips_other_release_without_claiming_jobs(workflow):
    directory,store,_,_=workflow
    clone_scope(workflow,'union_a','other')
    assert [item['scope_id'] for item in run_workflow_ticks(directory,'other')['processed']]==['union_a']
    assert all(job.spec.code_sha=='other' for job in store.list_jobs())
    assert run_workflow_ticks(directory,'absent')['selected_count']==0
    assert store._connection.execute("SELECT COUNT(*) FROM healthcheck_workflow_tick_cursors WHERE code_sha='absent'").fetchone()[0]==0


def test_tick_runner_fence_precedes_database_initialization(workflow,monkeypatch):
    directory,store,_,_=workflow
    (directory/'restore-lock.json').write_text('{}',encoding='utf-8')
    before=(directory/'state.sqlite').read_bytes()
    monkeypatch.setattr('executors.local.workflow_ticks.LocalHealthcheckWorkflows',unexpected_writer)
    with pytest.raises(ValueError,match='fenced'):run_workflow_ticks(directory,'code')
    assert (directory/'state.sqlite').read_bytes()==before and store.list_jobs()==()


def test_actual_bounded_tick_cli_only_enqueues_and_never_fetches_or_publishes(workflow):
    directory,store,_,_=workflow
    receipt=run_python_module('executors.local.workflow_ticks',('--workspace',str(directory),'--code-sha','code','--max-scopes','1'),seconds=30)
    assert receipt['outcome']=='completed'
    result=json.loads(receipt['stdout'])
    assert result['selected_count']==1 and result['processed'][0]['status']=='collecting' and result['network_providers']==[]
    assert store._connection.execute('SELECT COUNT(*) FROM collection_observations').fetchone()[0]==0
    assert store._connection.execute('SELECT COUNT(*) FROM publications').fetchone()[0]==0
    assert not (directory/'workbooks').exists()


def test_native_worker_automatically_schedules_followup_but_only_processes_one_job(workflow):
    from executors.local.isolated import run_isolated_worker

    directory,store,_,_=workflow
    result=run_isolated_worker(directory,'code',seconds=30)
    assert result['outcome']=='completed'
    processed=result['child_result']['processed']
    assert len(processed)==1 and processed[0]['command']=='refresh_roster_and_training'
    assert len(store.list_jobs())==2
    health=next(job for job in store.list_jobs() if job.spec.command=='recompute_healthcheck')
    assert health.state=='queued' and not (directory/'workbooks').exists()


@pytest.mark.parametrize('limit',[0,101,False])
def test_invalid_tick_bounds_do_not_modify_workspace(workflow,limit):
    directory,store,_,_=workflow
    before=(directory/'state.sqlite').read_bytes()
    with pytest.raises(ValueError):run_workflow_ticks(directory,'code',max_scopes=limit)
    assert (directory/'state.sqlite').read_bytes()==before and store.list_jobs()==()


def take_in_connection(path):
    with closing(LocalHealthcheckWorkflows(path)) as store:
        return store.take_workflow_scopes('code',1)


def unexpected_writer(*args,**kwargs):
    raise AssertionError('restore fence must precede writer initialization')


def test_invalid_computed_manifest_does_not_block_another_scopes_workflow(workflow):
    from application.tasks import TaskSpec

    directory,store,_,_=workflow
    bad=store.submit_job('bad_manifest',TaskSpec('recompute_healthcheck','bad_scope','code'),is_desired=True)
    store.complete_compute(store.claim('worker','code'),{})
    sheets=MockSheets(directory/'workbooks');sheets.create('mock_book','layout',{})
    result=run_worker(directory,'code',max_jobs=3,is_publish_enabled=True)
    assert result['processed'][0]['job_id']==bad.job_id and result['processed'][0]['state']=='failed'
    assert result['processed'][0]['error_code']=='worker_result_invalid'
    assert [job['state'] for job in result['processed'][1:]]==['succeeded','succeeded']
    assert result['workflow_ticks'][0]['status']=='health_published'
    assert store._connection.execute("SELECT COUNT(*) FROM publications WHERE state!='succeeded'").fetchone()[0]==0
    assert run_worker(directory,'code',is_publish_enabled=True)['processed']==[]


def test_computed_query_filters_unsupported_commands_before_limit(workflow):
    from application.tasks import TaskSpec

    _,store,_,_=workflow
    unsupported=store.submit_job('layout',TaskSpec('prepare_layout','layout_scope','code'),is_desired=True)
    store.complete_compute(store.claim('worker','code'),{'not_a_workbook':True})
    health=store.submit_job('health',TaskSpec('recompute_healthcheck','another_scope','code'),is_desired=True)
    store.complete_compute(store.claim('worker','code'),{})
    assert [job.job_id for job in store.computed_publication_jobs('code',1)]==[health.job_id]
    assert store.get_job(unsupported.job_id).state=='computed'


def test_invalid_typed_grid_is_rejected_before_any_publication_lock(workflow):
    from application.tasks import TaskSpec

    directory,store,_,_=workflow
    job=store.submit_job('invalid_grid',TaskSpec('recompute_healthcheck','bad_scope','code'),is_desired=True)
    store.complete_compute(store.claim('worker','code'),{'workbooks':{'bad_book':{'view':{},'view_id':'wrong'}},'layout_revisions':{'bad_book':'layout'}})
    result=run_worker(directory,'code',max_jobs=1,is_publish_enabled=True,max_workflow_checks=0)
    assert result['processed'][0]['state']=='failed' and store.get_job(job.job_id).error_code=='worker_result_invalid'
    assert store._connection.execute('SELECT COUNT(*) FROM publications').fetchone()[0]==0
    assert store._connection.execute('SELECT COUNT(*) FROM resource_locks').fetchone()[0]==0


def test_computed_rejection_never_revokes_an_existing_publisher(workflow):
    from application.tasks import TaskSpec

    _,store,_,_=workflow
    job=store.submit_job('claimed',TaskSpec('recompute_healthcheck','bad_scope','code'),is_desired=True)
    store.complete_compute(store.claim('worker','code'),{})
    claim=store.begin_publication(job.job_id,'publisher','code',('held_book',))
    assert store.reject_computed_result(job.job_id,'code').state=='publishing'
    store.mark_publication_uncertain(claim)
    assert store.reject_computed_result(job.job_id,'code').state=='publish_uncertain'
    assert store._connection.execute('SELECT COUNT(*) FROM resource_locks').fetchone()[0]==2
