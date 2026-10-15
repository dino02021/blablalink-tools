from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest

from adapters.local_healthcheck_workflows import LocalHealthcheckWorkflows
from adapters.mock_collection_script import capture_mock_collection_script
from application.compute_budget import ComputeBudget, ComputeLimit
from application.healthcheck_workflow import capture_healthcheck_workflow
from application.tasks import ResourceBusy, StateConflict, TaskSpec
from domain.mock_workspace import MOCK_WORKSPACE_MARKER
from domain.snapshots import capture_document, canonical_json
from executors.local.worker import run_worker
from local_tests.test_mock_collection_worker import script_sources
from local_tests.test_supplementary_collection import A, B, TIME, source, pages, rejoin_inputs


@pytest.fixture
def workflow(source):
    original, config, raw, reference, inputs, clock = source
    directory = Path(original._connection.execute('PRAGMA database_list').fetchone()[2]).parent
    (directory/'mock-workspace.json').write_text(canonical_json(MOCK_WORKSPACE_MARKER),encoding='utf-8')
    script = capture_mock_collection_script('union_demo',TIME,reference.envelope['reference_version'],script_sources(pages(reference,(A,B))))
    with closing(LocalHealthcheckWorkflows(directory/'state.sqlite',clock=lambda:clock[0])) as store:
        store.put_document(script)
        policy = capture_healthcheck_workflow('union_demo',TIME,reference.envelope['reference_version'],'code',reference.snapshot_id,raw.snapshot_id,script.snapshot_id)
        store.configure_healthcheck_workflow('policy',policy,policy.snapshot_id,0,'actor')
        yield directory,store,policy,source


def tick(store):
    return store.reconcile_healthcheck_workflow('union_demo','code',ComputeBudget())['state']


def test_durable_tick_collects_gap_then_schedules_same_version_healthcheck(workflow):
    directory,store,policy,_ = workflow
    first = tick(store)
    assert first['status']=='collecting' and first['supplementary_rounds']==1
    job_id = first['pending_collection_id']
    assert tick(store)==first and len(store.list_jobs())==1
    assert run_worker(directory,'code',max_jobs=1)['processed'][0]['state']=='succeeded'
    second = tick(store)
    assert second['status']=='health_pending' and second['pending_collection_id'] is None
    assert second['last_collection_id']==job_id and second['raw_snapshot_id']!=policy.payload['base_raw_id']
    health = store.get_job(second['last_healthcheck_id'])
    assert health.spec.command=='recompute_healthcheck'
    assert store.get_document(health.spec.version_map['training']).payload['raw_snapshot_id']==second['raw_snapshot_id']
    result = run_worker(directory,'code',max_jobs=1)
    assert result['processed'][0]['state']=='computed'
    assert tick(store)['status']=='health_computed'
    assert store._connection.execute('SELECT COUNT(*) FROM publications').fetchone()[0]==0


def test_threshold_edit_uses_current_config_without_new_collection(workflow):
    directory,store,_,_ = workflow
    tick(store)
    run_worker(directory,'code',max_jobs=1)
    ready = tick(store)
    run_worker(directory,'code',max_jobs=1)
    config_id = store.config_head('healthcheck','common')
    config = store.get_document(config_id)
    payload = config.payload
    payload['rules'][0]['metrics'][0]['threshold']=99
    changed = capture_document('healthcheck_config','common','2026-10-15T00:20:00Z',config.envelope['reference_version'],payload)
    store.publish_candidate(store.create_candidate(changed,config_id),())
    next_state = tick(store)
    health = store.get_job(next_state['last_healthcheck_id'])
    assert health.spec.version_map['healthcheck_config']==changed.snapshot_id
    assert next_state['raw_snapshot_id']==ready['raw_snapshot_id']
    assert len([job for job in store.list_jobs() if job.spec.command=='refresh_roster_and_training'])==1


def test_two_reconcilers_only_enqueue_one_fixed_collection(workflow):
    directory,store,_,_ = workflow
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(tick_in_connection,[directory/'state.sqlite']*2))
    assert result[0]['pending_collection_id']==result[1]['pending_collection_id']
    assert len(store.list_jobs())==1 and store.workflow_state('union_demo')['state']['supplementary_rounds']==1


def test_restart_and_policy_replay_do_not_reset_progress_or_rewind_policy(workflow):
    directory,store,policy,_ = workflow
    first = tick(store)
    receipt = store.configure_healthcheck_workflow('policy',policy,policy.snapshot_id,0,'actor')
    assert store.workflow_state('union_demo')['state']==first and receipt['revision']==1
    with closing(LocalHealthcheckWorkflows(directory/'state.sqlite')) as restarted:
        assert tick(restarted)==first
    disabled = capture_healthcheck_workflow('union_demo','2026-10-15T00:30:00Z',policy.envelope['reference_version'],
        'code',policy.payload['reference_id'],policy.payload['base_raw_id'],policy.payload['mock_script_id'],is_enabled=False)
    store.configure_healthcheck_workflow('disable',disabled,disabled.snapshot_id,1,'actor')
    assert tick(store)['status']=='disabled'
    store.configure_healthcheck_workflow('policy_replay',policy,policy.snapshot_id,0,'actor')
    assert store.workflow_policy('union_demo')['policy_id']==disabled.snapshot_id


def test_external_desired_owner_is_reported_without_competing_for_head(workflow):
    directory,store,_,_ = workflow
    tick(store);run_worker(directory,'code',max_jobs=1)
    ready = tick(store)
    health = store.get_job(ready['last_healthcheck_id'])
    other = store.submit_job('other_owner',replace(health.spec,intent_id='other_owner'),is_desired=True)
    state = tick(store)
    assert state['status']=='waiting_for_owner' and state['error_code']=='workflow_desired_conflict'
    assert store.is_job_current(other.job_id) and not store.is_job_current(health.job_id)
    assert tick(store)==state


def test_roster_rejoin_starts_new_epoch_cycle_and_replaces_old_pending_work(workflow):
    directory,store,_,original = workflow
    first = tick(store)
    rejoined = rejoin_inputs(original[0],original[4])
    next_state = tick(store)
    assert next_state['pending_collection_id']!=first['pending_collection_id']
    job = store.get_job(next_state['pending_collection_id'])
    inputs = store.get_document(job.spec.version_map['collection_inputs'])
    assert inputs.payload['member_epochs']==rejoined.payload['member_epochs']
    assert next_state['supplementary_rounds']==1


def test_bad_completed_observation_cannot_advance_workflow_raw_pointer(workflow):
    _,store,policy,_ = workflow
    first = tick(store)
    lease = store.claim('collector','code')
    store.complete_compute(lease,{'base_raw_id':'wrong','collection_inputs_id':'wrong'},is_publication_required=False)
    with pytest.raises(StateConflict): tick(store)
    current = store.workflow_state('union_demo')['state']
    assert current['raw_snapshot_id']==policy.payload['base_raw_id'] and current['pending_collection_id']==first['pending_collection_id']


def test_budget_limit_rolls_back_tick_without_fake_completion_or_attempt_reset(workflow):
    _,store,_,_ = workflow
    before = store.workflow_state('union_demo')
    with pytest.raises(ComputeLimit):
        store.reconcile_healthcheck_workflow('union_demo','code',ComputeBudget(max_checks=1))
    assert store.workflow_state('union_demo')==before and store.list_jobs()==()
    assert tick(store)['supplementary_rounds']==1


def test_wrong_release_and_configuration_cas_are_rejected_without_jobs(workflow):
    _,store,policy,_ = workflow
    with pytest.raises(StateConflict): store.reconcile_healthcheck_workflow('union_demo','other',ComputeBudget())
    with pytest.raises(StateConflict): store.configure_healthcheck_workflow('wrong',policy,'wrong',1,'actor')
    with pytest.raises(StateConflict): store.configure_healthcheck_workflow('wrong',policy,policy.snapshot_id,0,'different_actor')
    assert store.list_jobs()==()


def test_noop_tick_does_not_churn_workflow_state_revision(workflow):
    _,store,_,_ = workflow
    tick(store)
    before = store.workflow_state('union_demo')
    for _ in range(3): tick(store)
    assert store.workflow_state('union_demo')==before


def tick_in_connection(path):
    with closing(LocalHealthcheckWorkflows(path,clock=lambda:100)) as store:
        return tick(store)


def test_complete_but_still_unknown_data_is_limited_to_three_supplementary_rounds(workflow):
    directory,store,policy,_ = workflow
    script = store.get_document(policy.payload['mock_script_id'])
    sources = script.payload['sources']
    for item in sources:
        if item['member']['member_id']==A.member_id and item['source']=='details':
            item['pages'][0]['rows'][0].pop('skill1_lv')
    incomplete = capture_mock_collection_script('union_demo',TIME,script.envelope['reference_version'],sources)
    store.put_document(incomplete)
    bounded = capture_healthcheck_workflow('union_demo',TIME,policy.envelope['reference_version'],'code',policy.payload['reference_id'],
        policy.payload['base_raw_id'],incomplete.snapshot_id)
    store.configure_healthcheck_workflow('bounded',bounded,bounded.snapshot_id,1,'actor')
    for round_number in range(1,4):
        state = tick(store)
        assert state['status']=='collecting' and state['supplementary_rounds']==round_number
        result = run_worker(directory,'code',max_jobs=1)
        assert result['processed'][0]['state']=='succeeded'
    stopped = tick(store)
    assert stopped['status']=='capacity_blocked' and stopped['error_code']=='supplementary_round_limit'
    assert len(store.list_jobs())==3 and tick(store)==stopped
    with closing(LocalHealthcheckWorkflows(directory/'state.sqlite')) as restarted:
        assert tick(restarted)==stopped and len(restarted.list_jobs())==3


def test_auth_collection_is_not_automatically_requeued_by_followup_ticks(workflow):
    directory,store,policy,_ = workflow
    script = store.get_document(policy.payload['mock_script_id'])
    sources = script.payload['sources']
    for item in sources:
        if item['member']['member_id']==A.member_id and item['source']=='ownership':
            item.update(outcome='auth_required',pages=[])
    blocked_script = capture_mock_collection_script('union_demo',TIME,script.envelope['reference_version'],sources)
    store.put_document(blocked_script)
    blocked_policy = capture_healthcheck_workflow('union_demo',TIME,policy.envelope['reference_version'],'code',policy.payload['reference_id'],
        policy.payload['base_raw_id'],blocked_script.snapshot_id)
    store.configure_healthcheck_workflow('auth',blocked_policy,blocked_policy.snapshot_id,1,'actor')
    tick(store);run_worker(directory,'code',max_jobs=1)
    state = tick(store)
    assert state['status']=='auth_required' and state['raw_snapshot_id']==policy.payload['base_raw_id']
    assert tick(store)==state and len(store.list_jobs())==1


def test_unresolved_publisher_retains_new_observation_without_enqueuing_health(workflow):
    from adapters.mock_sheets import MockSheets
    from application.publication import publish_workbooks

    directory,store,_,_ = workflow
    tick(store);run_worker(directory,'code',max_jobs=1)
    other = store.submit_job('other',TaskSpec('prepare_layout','other','code',family='layout'),is_desired=True)
    manifest = {'workbooks':{'held_book':{'mock':True}},'layout_revisions':{'held_book':'layout'}}
    store.complete_compute(store.claim('other','code',commands=('prepare_layout',)),manifest)
    claim = store.begin_publication(other.job_id,'publisher','code',('held_book',))
    waiting = tick(store)
    assert waiting['status']=='waiting_for_publisher' and waiting['pending_collection_id'] is None
    assert waiting['last_collection_id'] and not waiting['last_healthcheck_id']
    sheets = MockSheets(directory/'workbooks');sheets.create('held_book','layout',{})
    publish_workbooks(store,sheets,claim,manifest['workbooks'],manifest['layout_revisions'])
    ready = tick(store)
    assert ready['status']=='health_pending' and ready['raw_snapshot_id']==waiting['raw_snapshot_id']
    assert len([job for job in store.list_jobs() if job.spec.command=='refresh_roster_and_training'])==1


def test_missing_synthetic_member_data_waits_without_fabricating_training(workflow):
    from application.roster_lifecycle import RosterMember, capture_roster_observation, prepare_roster_change
    from domain.snapshots import MemberKey

    _,store,_,original = workflow
    registry,_,_,reference,inputs,_ = original
    new = MemberKey('area_demo','new_member')
    observation = capture_roster_observation('union_demo',TIME,reference.envelope['reference_version'],
        tuple(RosterMember(member,'=same') for member in (A,B,new)),'complete','joined')
    plan = prepare_roster_change(inputs,observation,'code')
    candidate = registry.create_roster_candidate(plan,observation)
    registry.adopt_roster_candidate('joined',candidate,candidate,'actor','code')
    state = tick(store)
    assert state['status']=='waiting_for_mock_data' and state['error_code']=='mock_member_fixture_missing'
    assert store.list_jobs()==()


def test_empty_inactive_scope_clears_pending_scheduler_pointer_without_new_jobs(workflow):
    from application.roster_lifecycle import capture_roster_observation, prepare_roster_change

    _,store,_,original = workflow
    first = tick(store)
    registry,_,_,reference,inputs,_ = original
    observation = capture_roster_observation('union_demo',TIME,reference.envelope['reference_version'],(),'complete','inactive')
    plan = prepare_roster_change(inputs,observation,'code')
    candidate = registry.create_roster_candidate(plan,observation)
    registry.adopt_roster_candidate('inactive',candidate,candidate,'actor','code')
    state = tick(store)
    assert state['status']=='inactive' and state['pending_collection_id'] is None
    assert len(store.list_jobs())==1 and not store.is_job_current(first['pending_collection_id'])


def test_config_return_to_previous_content_uses_revision_and_reasserts_desired(workflow):
    directory,store,_,_ = workflow
    tick(store);run_worker(directory,'code',max_jobs=1)
    original_state = tick(store)
    run_worker(directory,'code',max_jobs=1)
    original_config = store.get_document(store.config_head('healthcheck','common'))
    data = original_config.payload
    data['rules'][0]['metrics'][0]['threshold']=99
    changed = capture_document('healthcheck_config','common','2026-10-15T00:30:00Z',original_config.envelope['reference_version'],data)
    store.publish_candidate(store.create_candidate(changed,original_config.snapshot_id),())
    newer = tick(store)
    assert newer['last_healthcheck_id']!=original_state['last_healthcheck_id']
    store.publish_candidate(store.create_candidate(original_config,changed.snapshot_id),())
    restored = tick(store)
    assert restored['last_healthcheck_id']==original_state['last_healthcheck_id']
    assert restored['cycle_key']!=original_state['cycle_key']
    assert store.is_job_current(restored['last_healthcheck_id']) and not store.is_job_current(newer['last_healthcheck_id'])
    assert restored['status']=='health_computed'


def test_status_exposes_workflow_without_enqueuing_or_mutating_state(workflow):
    from adapters.local_system_status import read_system_status
    from application.system_status_view import render_system_status

    directory,store,_,_ = workflow
    scheduled = tick(store)
    before = (directory/'state.sqlite').read_bytes()
    report = read_system_status(directory,('union_demo',))
    assert report['scopes'][0]['healthcheck_workflow']['state']==scheduled
    tab = render_system_status(report)
    assert len(tab.column_ids)==18
    assert tab.rows[1][tab.column_ids.index('healthcheck_workflow_status')].value=='collecting'
    assert (directory/'state.sqlite').read_bytes()==before and len(store.list_jobs())==1


def test_bad_workflow_state_isolated_to_scope_in_read_only_status(workflow):
    from adapters.local_system_status import read_system_status

    directory,store,_,_ = workflow
    store._connection.execute("UPDATE healthcheck_workflow_state SET state_json='{}'")
    result = read_system_status(directory,('union_demo','another_union'))
    assert not result['scopes'][0]['is_available'] and result['scopes'][1]['is_available']


def test_followup_observation_to_actual_mock_publication_preserves_human_tab(workflow):
    from adapters.mock_sheets import MockSheets

    directory,store,_,_ = workflow
    sheets = MockSheets(directory/'workbooks')
    sheets.create('mock_book','layout',{'manual':0})
    sheets.set_manual_tab('mock_book','notes','人工備註',[['=literal',0,'']])
    before = sheets.read('mock_book')
    tick(store);run_worker(directory,'code',max_jobs=1)
    ready = tick(store)
    result = run_worker(directory,'code',max_jobs=1,is_publish_enabled=True)
    assert result['processed'][0]['job_id']==ready['last_healthcheck_id'] and result['processed'][0]['state']=='succeeded'
    assert tick(store)['status']=='health_published'
    book = sheets.read('mock_book')
    assert book['inputs']==before['inputs'] and book['manual_tabs']==before['manual_tabs']
    assert book['generated']['snapshot_id']==store.get_job(ready['last_healthcheck_id']).spec.version_map['training']


def test_policy_head_state_and_receipt_rollback_together_on_database_failure(workflow):
    _,store,policy,_ = workflow
    tick(store)
    before = store.workflow_state('union_demo')
    disabled = capture_healthcheck_workflow('union_demo','2026-10-15T00:40:00Z',policy.envelope['reference_version'],'code',
        policy.payload['reference_id'],policy.payload['base_raw_id'],policy.payload['mock_script_id'],is_enabled=False)
    store._connection.execute("CREATE TRIGGER fail_workflow_receipt BEFORE INSERT ON healthcheck_workflow_policy_receipts BEGIN SELECT RAISE(ABORT,'synthetic'); END")
    with pytest.raises(Exception,match='synthetic'):
        store.configure_healthcheck_workflow('disable',disabled,disabled.snapshot_id,1,'actor')
    assert store.workflow_policy('union_demo')['policy_id']==policy.snapshot_id
    assert store.workflow_state('union_demo')==before
    store._connection.execute('DROP TRIGGER fail_workflow_receipt')
    assert store.configure_healthcheck_workflow('disable',disabled,disabled.snapshot_id,1,'actor')['revision']==2
