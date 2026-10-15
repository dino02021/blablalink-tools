from contextlib import closing
from dataclasses import replace
import json

import pytest

from adapters.local_backup import create_backup, restore_backup
from adapters.local_publication_safety import LocalPublicationSafety
from adapters.local_retention import LocalObjectRetention
from adapters.local_system_status import read_system_status
from adapters.mock_sheets import MockSheets
from application.publication import publish_workbooks
from application.publication_safety import prepare_publication_abort
from application.tasks import LeaseLost, StateConflict, TaskSpec
from domain.mock_workspace import MOCK_WORKSPACE_MARKER
from domain.snapshots import capture_document, canonical_json


@pytest.fixture
def publication(tmp_path):
    directory=tmp_path/'mock';directory.mkdir()
    (directory/'mock-workspace.json').write_text(canonical_json(MOCK_WORKSPACE_MARKER),encoding='utf-8')
    with closing(LocalPublicationSafety(directory/'state.sqlite')) as store:
        sheets=MockSheets(directory/'workbooks')
        outputs={'book_a':{'score':100},'book_b':{'score':50}}
        revisions={key:'layout' for key in outputs}
        for resource in outputs:sheets.create(resource,'layout',{'manual':0})
        job=store.submit_job('original',TaskSpec('recompute_healthcheck','union_demo','code'),is_desired=True)
        store.complete_compute(store.claim('worker','code'),{'workbooks':outputs,'layout_revisions':revisions})
        claim=store.begin_publication(job.job_id,'publisher','code',tuple(outputs))
        yield directory,store,sheets,claim,outputs,revisions


def before_fault(publication):
    _,store,sheets,claim,outputs,revisions=publication
    with pytest.raises(TimeoutError):publish_workbooks(store,sheets,claim,outputs,revisions,{'book_a':'before'})
    assert store.get_job(claim.job_id).state=='publish_uncertain'
    return prepare_publication_abort(store,sheets,claim.publication_id,'reconciler',True)


def commit(store,plan,request='abort'):
    return store.commit_publication_abort(request,plan,plan.snapshot_id,True)


def test_never_started_publication_can_be_aborted_without_reading_or_writing_sheet(publication):
    _,store,_,claim,_,_=publication
    plan=prepare_publication_abort(store,NoReadSheets(),claim.publication_id,'reconciler',True)
    assert plan.payload['abort_mode']=='not_started' and plan.payload['bases']==plan.payload['observations']=={}
    receipt=commit(store,plan)
    assert receipt['outcome']=='aborted_not_started'
    assert store.publication_safety_metadata(claim.publication_id)['effective_state']=='aborted'
    assert store.get_job(claim.job_id).state=='failed' and not store.has_successful_publication(claim.job_id)
    assert store._connection.execute('SELECT COUNT(*) FROM resource_locks').fetchone()[0]==0


def test_unknown_before_write_requires_all_baselines_and_preserves_later_human_edits(publication):
    directory,store,sheets,claim,_,_=publication
    before_fault(publication)
    sheets.edit_inputs('book_a',{'manual':'collaborator changed'})
    sheets.set_manual_tab('book_a','notes','人工備註',[['=literal',0,'']])
    before={resource:sheets.read(resource) for resource in claim.resources}
    plan=prepare_publication_abort(store,sheets,claim.publication_id,'reconciler',True)
    assert plan.payload['abort_mode']=='unchanged' and set(plan.payload['bases'])==set(claim.resources)
    receipt=commit(store,plan)
    assert receipt['outcome']=='aborted_unchanged'
    assert all(sheets.read(resource)==before[resource] for resource in claim.resources) and sheets.write_count==0
    state=read_system_status(directory,('union_demo',))
    assert not state['global']['is_write_blocked'] and state['scopes'][0]['last_published'] is None
    assert store.publication_safety_metadata(claim.publication_id)['state']=='publish_uncertain'  # legacy history retained
    assert store.publication_safety_metadata(claim.publication_id)['effective_state']=='aborted'


@pytest.mark.parametrize('operation',['prepare','commit'])
def test_writer_stop_is_explicit_and_never_replaced_by_elapsed_time(publication,operation):
    _,store,sheets,claim,_,_=publication
    plan=before_fault(publication)
    store._clock=lambda:999999999999
    with pytest.raises(StateConflict,match='stop'):
        if operation=='prepare':prepare_publication_abort(store,NoReadSheets(),claim.publication_id,'reconciler',False)
        else:store.commit_publication_abort('abort',plan,plan.snapshot_id,False)
    assert store.get_job(claim.job_id).state=='publish_uncertain'
    assert store._connection.execute('SELECT COUNT(*) FROM resource_locks').fetchone()[0]==3


def test_any_partial_application_refuses_abort_and_retry_preserves_original_baselines(publication):
    _,store,sheets,claim,outputs,revisions=publication
    with pytest.raises(TimeoutError):publish_workbooks(store,sheets,claim,outputs,revisions,{'book_b':'before'})
    original={resource:store.publication_base(claim.publication_id,resource).snapshot_id for resource in claim.resources}
    with pytest.raises(StateConflict,match='unchanged'):prepare_publication_abort(store,sheets,claim.publication_id,'reconciler',True)
    observations={resource:sheets.observe(resource,outputs[resource],claim.commit_id) for resource in claim.resources}
    replacement=store.recover_publication(claim.publication_id,'replacement',observations,True)
    publish_workbooks(store,sheets,replacement,outputs,revisions)
    assert store.get_job(claim.job_id).state=='succeeded' and sheets.write_count==2
    assert {resource:store.publication_base(claim.publication_id,resource).snapshot_id for resource in claim.resources}==original


def test_applied_response_loss_uses_success_recovery_instead_of_abort(publication):
    _,store,sheets,claim,outputs,revisions=publication
    with pytest.raises(TimeoutError):publish_workbooks(store,sheets,claim,outputs,revisions,{'book_b':'after'})
    with pytest.raises(StateConflict):prepare_publication_abort(store,sheets,claim.publication_id,'reconciler',True)
    observations={resource:sheets.observe(resource,outputs[resource],claim.commit_id) for resource in claim.resources}
    assert store.recover_publication(claim.publication_id,'replacement',observations,True) is None
    assert store.get_job(claim.job_id).state=='succeeded' and sheets.write_count==2
    assert store._connection.execute('SELECT COUNT(*) FROM publication_abort_receipts').fetchone()[0]==0


def test_missing_baseline_after_send_cannot_be_retrofitted(publication):
    _,store,sheets,claim,outputs,_=publication
    store.mark_publication_send_started(claim)
    store.mark_publication_uncertain(claim)
    with pytest.raises(StateConflict,match='baseline'):prepare_publication_abort(store,sheets,claim.publication_id,'reconciler',True)
    replacement=store.recover_publication(claim.publication_id,'replacement',{resource:None for resource in claim.resources},True)
    fresh=sheets.capture_publication_state('book_a',claim.publication_id,claim.commit_id,store.get_job(claim.job_id).result_id,claim.code_sha)
    with pytest.raises(StateConflict,match='precede'):store.record_publication_base(replacement,'book_a',fresh)


def test_legacy_publication_without_safety_phase_is_never_inferred_unsent(publication):
    _,store,sheets,claim,outputs,revisions=publication
    store._connection.execute('DELETE FROM publication_safety_phases WHERE publication_id=?',(claim.publication_id,))
    with pytest.raises(StateConflict,match='baseline'):prepare_publication_abort(store,sheets,claim.publication_id,'reconciler',True)
    publish_workbooks(store,sheets,claim,outputs,revisions)
    assert store.get_job(claim.job_id).state=='succeeded'
    assert all(store.publication_base(claim.publication_id,resource) is None for resource in claim.resources)


def test_abort_confirmation_and_generation_are_both_fixed(publication):
    _,store,sheets,claim,_,_=publication
    plan=before_fault(publication)
    with pytest.raises(StateConflict):store.commit_publication_abort('abort',plan,'wrong',True)
    replacement=store.recover_publication(claim.publication_id,'replacement',{resource:None for resource in claim.resources},True)
    with pytest.raises(StateConflict,match='generation'):commit(store,plan)
    current=prepare_publication_abort(store,sheets,claim.publication_id,'reconciler',True)
    assert current.payload['generation']==replacement.generation
    assert commit(store,current)['outcome']=='aborted_unchanged'


def test_forged_never_started_plan_does_not_bypass_a_send_marker(publication):
    _,store,_,_,_,_=publication
    plan=before_fault(publication)
    data={**plan.payload,'abort_mode':'not_started','bases':{},'observations':{}}
    forged=capture_document('union_inputs',plan.envelope['scope_id'],plan.envelope['captured_at'],plan.envelope['reference_version'],data)
    with pytest.raises(StateConflict,match='send'):commit(store,forged)
    assert store._connection.execute('SELECT COUNT(*) FROM publication_abort_receipts').fetchone()[0]==0


def test_abort_is_atomic_if_receipt_storage_fails(publication):
    _,store,_,claim,_,_=publication
    plan=before_fault(publication)
    before=store.publication_safety_metadata(claim.publication_id)
    store._connection.execute("CREATE TRIGGER fail_abort BEFORE INSERT ON publication_abort_receipts BEGIN SELECT RAISE(ABORT,'synthetic'); END")
    with pytest.raises(Exception,match='synthetic'):commit(store,plan)
    assert store.publication_safety_metadata(claim.publication_id)==before
    assert store._connection.execute('SELECT COUNT(*) FROM resource_locks').fetchone()[0]==3
    store._connection.execute('DROP TRIGGER fail_abort')
    assert commit(store,plan)['outcome']=='aborted_unchanged'


def test_abort_refuses_damaged_lock_inventory_without_clearing_other_locks(publication):
    _,store,_,claim,_,_=publication
    plan=before_fault(publication)
    store._connection.execute("DELETE FROM resource_locks WHERE resource_id='sheet:book_a'")
    with pytest.raises(StateConflict,match='lock'):commit(store,plan)
    assert store.get_job(claim.job_id).state=='publish_uncertain'
    assert store._connection.execute('SELECT COUNT(*) FROM resource_locks').fetchone()[0]==2


def test_abort_replay_does_not_release_a_new_publishers_locks_or_reactivate_old_writer(publication):
    _,store,sheets,claim,outputs,revisions=publication
    plan=before_fault(publication)
    receipt=commit(store,plan)
    spec=replace(store.get_job(claim.job_id).spec,intent_id='new_publication')
    new=store.submit_job('new_task',spec,is_desired=True)
    store.complete_compute(store.claim('new_worker',claim.code_sha),{'workbooks':outputs,'layout_revisions':revisions})
    new_claim=store.begin_publication(new.job_id,'new_publisher',claim.code_sha,claim.resources)
    assert commit(store,plan,'alias')==receipt
    assert store._connection.execute('SELECT DISTINCT publication_id FROM resource_locks').fetchone()[0]==new_claim.publication_id
    with pytest.raises((LeaseLost,StateConflict)):publish_workbooks(store,NoReadSheets(),claim,outputs,revisions)
    with pytest.raises(StateConflict):store.recover_publication(claim.publication_id,'forged',{resource:None for resource in claim.resources},True)
    publish_workbooks(store,sheets,new_claim,outputs,revisions)
    assert store.get_job(new.job_id).state=='succeeded' and store.get_job(claim.job_id).state=='failed'


def test_reusing_abort_request_with_another_actor_plan_is_rejected(publication):
    _,store,_,_,_,_=publication
    plan=before_fault(publication);commit(store,plan)
    changed=capture_document('union_inputs',plan.envelope['scope_id'],plan.envelope['captured_at'],plan.envelope['reference_version'],{**plan.payload,'actor_id':'other'})
    with pytest.raises(StateConflict):commit(store,changed)
    with pytest.raises(StateConflict):commit(store,changed,'new_request')


def test_marker_without_full_generated_content_match_cannot_prove_no_write(publication):
    _,store,sheets,claim,outputs,_=publication
    before_fault(publication)
    book=sheets.read('book_a');book['commit_id']=claim.commit_id;book['payload_hash']='a'*64
    sheets._save('book_a',book)
    assert sheets.observe('book_a',outputs['book_a'],claim.commit_id) is None
    with pytest.raises(StateConflict):prepare_publication_abort(store,sheets,claim.publication_id,'reconciler',True)
    assert store.get_job(claim.job_id).state=='publish_uncertain'


def test_baseline_cannot_be_overwritten_after_send_or_recovery(publication):
    _,store,sheets,claim,_,_=publication
    before_fault(publication)
    base=store.publication_base(claim.publication_id,'book_a')
    replacement=store.recover_publication(claim.publication_id,'replacement',{resource:None for resource in claim.resources},True)
    assert store.record_publication_base(replacement,'book_a',base)==base.snapshot_id
    book=sheets.read('book_a');book['layout_revision']='changed';sheets._save('book_a',book)
    changed=sheets.capture_publication_state('book_a',claim.publication_id,claim.commit_id,store.get_job(claim.job_id).result_id,claim.code_sha)
    with pytest.raises(StateConflict,match='overwritten'):store.record_publication_base(replacement,'book_a',changed)
    assert store.publication_base(claim.publication_id,'book_a')==base


def test_abort_history_and_baselines_survive_retention_backup_and_restore(publication,tmp_path):
    directory,store,_,claim,_,_=publication
    plan=before_fault(publication);receipt=commit(store,plan)
    with closing(LocalPublicationSafety(directory/'state.sqlite')) as restarted:
        assert restarted.publication_abort_receipt(receipt['receipt_id'])==receipt
    with closing(LocalObjectRetention(directory/'state.sqlite')) as retention:
        candidates=set(retention.plan_retention(999999999999).object_ids)
        assert plan.snapshot_id not in candidates and not (set(plan.payload['bases'].values())&candidates)
    archive=tmp_path/'backup.zip'
    backup=create_backup(directory,archive,'code')
    restored=tmp_path/'restored'
    restore_backup(archive,restored,backup['backup_id'],'code')
    with closing(LocalPublicationSafety(restored/'state.sqlite')) as clone:
        assert clone.publication_abort_receipt(receipt['receipt_id'])==receipt
        assert clone.publication_safety_metadata(claim.publication_id)['effective_state']=='aborted'
    assert (restored/'restore-lock.json').exists()


class NoReadSheets:
    def capture_publication_state(self,*args,**kwargs):
        raise AssertionError('not-started or missing stop proof must not query sheets')


def test_real_typed_healthcheck_baseline_requires_full_grid_restore_and_keeps_human_edits(tmp_path):
    from application.sheet_healthcheck import run_healthcheck_job
    from executors.local.demo import run_demo

    directory=tmp_path/'typed';summary=run_demo(directory)
    with closing(LocalPublicationSafety(directory/'state.sqlite')) as store:
        original=store.get_job(summary['job_id'])
        next_job=store.submit_job('next_health',replace(original.spec,intent_id='next_health'),is_desired=True)
        computed=run_healthcheck_job(store,store.claim('worker',original.spec.code_sha))
        assert computed.job_id==next_job.job_id
        manifest=store.get_result(computed.result_id)
        sheets=MockSheets(directory/'workbooks')
        claim=store.begin_publication(computed.job_id,'publisher',original.spec.code_sha,tuple(manifest['workbooks']))
        with pytest.raises(TimeoutError):publish_workbooks(store,sheets,claim,manifest['workbooks'],manifest['layout_revisions'],{'output_demo':'before'})
        before=sheets.read('output_demo')
        assert len(before['generated_grid']['tabs'])==8
        tampered=sheets.read('output_demo')
        first=next(iter(tampered['generated_grid']['tabs'].values()))
        first['frozen_rows']+=1
        sheets._save('output_demo',tampered)
        with pytest.raises(StateConflict):prepare_publication_abort(store,sheets,claim.publication_id,'reconciler',True)
        sheets._save('output_demo',before)  # explicit mock simulation of full generated-area restoration
        sheets.edit_inputs('output_demo',{'manual_notes':{'member_a':'new human note'}})
        plan=prepare_publication_abort(store,sheets,claim.publication_id,'reconciler',True)
        receipt=commit(store,plan)
        assert receipt['outcome']=='aborted_unchanged'
        assert sheets.read('output_demo')['generated_grid']==before['generated_grid']
        assert sheets.read('output_demo')['inputs']['manual_notes']['member_a']=='new human note'
        report=read_system_status(directory,('union_demo',))
        assert report['scopes'][0]['last_published']['job_id']==original.job_id
        assert not report['global']['is_write_blocked']
