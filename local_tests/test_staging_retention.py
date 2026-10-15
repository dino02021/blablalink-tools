"""Bounded transport cleanup uses real SQLite transactions and synthetic workbooks."""

from contextlib import closing
from dataclasses import replace
import sqlite3

import pytest

from adapters.local_publication_safety import LocalPublicationSafety
from adapters.local_staging_retention import LocalStagingRetention
from adapters.local_workbook_staging import LocalWorkbookStaging
from adapters.mock_sheets import MockSheets
from application.compute_budget import ComputeBudget, ComputeLimit
from application.publication import publish_workbooks
from application.publication_safety import prepare_publication_abort
from application.tasks import ResourceBusy, StateConflict, TaskSpec
from application.workbook_staging import StagedSheets, prepare_workbook_stage
from domain.chunks import encode_document
from domain.execution import execution_checks
from domain.mock_workspace import MOCK_WORKSPACE_MARKER
from domain.snapshots import canonical_json, capture_document


@pytest.fixture
def workspace(tmp_path):
    directory=tmp_path/'mock'
    directory.mkdir()
    (directory/'mock-workspace.json').write_text(canonical_json(MOCK_WORKSPACE_MARKER),encoding='utf-8')
    with closing(LocalPublicationSafety(directory/'state.sqlite')) as store:
        sheets=MockSheets(directory/'workbooks')
        sheets.create('book','layout',{'human':'keep'})
        with closing(LocalWorkbookStaging(directory/'staging.sqlite')) as staging:
            job=store.submit_job('job',TaskSpec('recompute_healthcheck','union','release'),is_desired=True)
            result={'workbooks':{'book':{'score':100}},'layout_revisions':{'book':'layout'}}
            store.complete_compute(store.claim('worker','release'),result)
            claim=store.begin_publication(job.job_id,'publisher','release',('book',))
            stage,chunks=prepare_workbook_stage(store,sheets,claim,'book')
            staging.begin_stage(stage)
            for i,chunk in enumerate(chunks):staging.put_stage_chunk(stage.stage_id,i,chunk)
            staging.seal_stage(stage.stage_id)
            publish_workbooks(store,StagedSheets(store,sheets,staging,{'book':stage.stage_id}),claim,result['workbooks'],result['layout_revisions'])
    return directory,stage,chunks,sheets


def test_terminal_stage_and_chunks_deleted_with_receipt_and_history_preserved(workspace):
    directory,stage,chunks,sheets=workspace
    before=sheets.read('book')
    with closing(LocalStagingRetention(directory)) as cleanup:
        plan=cleanup.plan_retention(99999999999,is_all_writers_stopped=True)
        assert plan.stage_ids==(stage.stage_id,) and plan.object_ids==(stage.manifest.object_id,)
        assert plan.chunk_bytes==sum(map(len,chunks))
        receipt=cleanup.apply_retention(plan,plan.plan_id,True)
        assert receipt['deleted_chunk_bytes']==plan.chunk_bytes
        assert cleanup.staging.connection.execute('SELECT COUNT(*) FROM object_chunks').fetchone()[0]==0
        assert cleanup.staging.connection.execute('SELECT COUNT(*) FROM chunk_manifests').fetchone()[0]==0
        assert cleanup.store.has_successful_publication(stage.job_id)
        assert cleanup.store.get_result(stage.result_id)['workbooks']['book']=={'score':100}
        assert cleanup.apply_retention(plan,plan.plan_id,True)==receipt
    assert sheets.read('book')==before


def test_replay_does_not_delete_later_recreated_transport(workspace):
    directory,stage,chunks,_=workspace
    with closing(LocalStagingRetention(directory)) as cleanup:
        plan=cleanup.plan_retention(99999999999,is_all_writers_stopped=True)
        receipt=cleanup.apply_retention(plan,plan.plan_id,True)
        cleanup.staging.begin_stage(stage)
        cleanup.staging.put_stage_chunk(stage.stage_id,0,chunks[0])
        assert cleanup.apply_retention(plan,plan.plan_id,True)==receipt
        assert cleanup.staging.state(stage.stage_id)=='preparing'


def test_cutoff_retains_recent_publication(workspace):
    with closing(LocalStagingRetention(workspace[0])) as cleanup:
        assert cleanup.plan_retention(0,is_all_writers_stopped=True).stage_ids==()


@pytest.mark.parametrize('state',['preparing','sealed','cancelled','activation_uncertain'])
def test_shared_manifest_is_kept_for_any_remaining_stage(workspace,state):
    directory,stage,_,_=workspace
    with closing(LocalStagingRetention(directory)) as cleanup:
        sibling=replace(stage,base_commit_id='another-base')
        cleanup.staging.begin_stage(sibling)
        cleanup.staging.connection.execute('UPDATE workbook_stages SET state=? WHERE stage_id=?',(state,sibling.stage_id))
        cleanup.staging.connection.commit()
        # Make the sibling belong to an unproven publication while sharing exactly
        # the same immutable transport object.
        orphan=replace(sibling,job_id='unknown-job')
        cleanup.staging.connection.execute('DELETE FROM workbook_stages WHERE stage_id=?',(sibling.stage_id,))
        cleanup.staging.connection.execute('INSERT INTO workbook_stages VALUES(?,?,?)',(orphan.stage_id,orphan.serialized,state))
        cleanup.staging.connection.commit()
        plan=cleanup.plan_retention(99999999999,is_all_writers_stopped=True)
        assert plan.stage_ids==(stage.stage_id,) and plan.object_ids==()
        cleanup.apply_retention(plan,plan.plan_id,True)
        assert cleanup.staging.read(stage.manifest.object_id).snapshot_id==stage.manifest.object_id


def test_uncertain_activation_is_never_inferred_safe_from_success_receipt(workspace):
    with closing(LocalStagingRetention(workspace[0])) as cleanup:
        cleanup.staging.connection.execute("UPDATE workbook_stages SET state='activation_uncertain'")
        cleanup.staging.connection.commit()
        assert cleanup.plan_retention(99999999999,is_all_writers_stopped=True).stage_ids==()


def test_explicit_stop_and_fixed_confirmation_required(workspace):
    with closing(LocalStagingRetention(workspace[0])) as cleanup:
        with pytest.raises(StateConflict,match='stopped'):cleanup.plan_retention(99999999999)
        plan=cleanup.plan_retention(99999999999,is_all_writers_stopped=True)
        with pytest.raises(StateConflict):cleanup.apply_retention(plan,'0'*64,True)
        with pytest.raises(StateConflict,match='stopped'):cleanup.apply_retention(plan,plan.plan_id)


def test_changed_inventory_rejects_plan_without_partial_cleanup(workspace):
    directory,stage,_,_=workspace
    with closing(LocalStagingRetention(directory)) as cleanup:
        plan=cleanup.plan_retention(99999999999,is_all_writers_stopped=True)
        cleanup.staging.connection.execute("UPDATE workbook_stages SET state='sealed'")
        cleanup.staging.connection.commit()
        with pytest.raises(StateConflict,match='changed'):cleanup.apply_retention(plan,plan.plan_id,True)
        assert cleanup.staging.state(stage.stage_id)=='sealed'


def test_delete_failure_rolls_back_stages_chunks_and_receipt(workspace):
    directory,stage,_,_=workspace
    with closing(LocalStagingRetention(directory)) as cleanup:
        plan=cleanup.plan_retention(99999999999,is_all_writers_stopped=True)
        cleanup.staging.connection.execute("CREATE TRIGGER deny_chunk_delete BEFORE DELETE ON object_chunks BEGIN SELECT RAISE(ABORT,'injected'); END")
        with pytest.raises(sqlite3.IntegrityError,match='injected'):cleanup.apply_retention(plan,plan.plan_id,True)
        assert cleanup.staging.state(stage.stage_id)=='activated'
        assert cleanup.staging.connection.execute('SELECT COUNT(*) FROM staging_retention_receipts').fetchone()[0]==0
        cleanup.staging.connection.execute('DROP TRIGGER deny_chunk_delete')
        assert cleanup.apply_retention(plan,plan.plan_id,True)['stage_ids']==[stage.stage_id]


def test_budget_interrupt_rolls_back_inventory_read(workspace):
    directory,stage,_,_=workspace
    with closing(LocalStagingRetention(directory)) as cleanup:
        with pytest.raises(ComputeLimit):
            with execution_checks(ComputeBudget(max_checks=1).check):cleanup.plan_retention(99999999999,is_all_writers_stopped=True)
        assert cleanup.staging.state(stage.stage_id)=='activated'


def test_corrupt_chunk_fails_closed_and_preserves_every_stage(workspace):
    directory,stage,_,_=workspace
    with closing(LocalStagingRetention(directory)) as cleanup:
        cleanup.staging.connection.execute("UPDATE object_chunks SET content=X'00'")
        cleanup.staging.connection.commit()
        with pytest.raises(ValueError,match='integrity'):cleanup.plan_retention(99999999999,is_all_writers_stopped=True)
        assert cleanup.staging.state(stage.stage_id)=='activated'


def test_standalone_chunks_without_stages_are_retained(workspace):
    directory,stage,_,_=workspace
    with closing(LocalStagingRetention(directory)) as cleanup:
        document=capture_document('union_inputs','other','2026-10-16T00:00:00Z','reference',{'example':1})
        manifest,chunks=encode_document(document)
        cleanup.staging.begin(manifest)
        for i,chunk in enumerate(chunks):cleanup.staging.put(manifest.object_id,i,chunk)
        cleanup.staging.seal(manifest.object_id)
        plan=cleanup.plan_retention(99999999999,is_all_writers_stopped=True)
        assert manifest.object_id not in plan.object_ids
        cleanup.apply_retention(plan,plan.plan_id,True)
        assert cleanup.staging.read(manifest.object_id)==document


def test_unknown_publisher_blocks_even_unrelated_terminal_cleanup(workspace):
    directory,stage,_,_=workspace
    with closing(LocalStagingRetention(directory)) as cleanup:
        job=cleanup.store.submit_job('other',TaskSpec('recompute_healthcheck','other','release'))
        cleanup.store.complete_compute(cleanup.store.claim('worker','release'),{'workbooks':{'another':{}},'layout_revisions':{'another':'layout'}})
        claim=cleanup.store.begin_publication(job.job_id,'publisher','release',('another',))
        cleanup.store.mark_publication_uncertain(claim)
        with pytest.raises(ResourceBusy):cleanup.plan_retention(99999999999,is_all_writers_stopped=True)
        assert cleanup.staging.state(stage.stage_id)=='activated'


def test_computed_job_with_no_terminal_publication_is_retained(workspace):
    directory,stage,_,_=workspace
    with closing(LocalStagingRetention(directory)) as cleanup:
        cleanup.store._connection.execute("UPDATE jobs SET state='computed' WHERE job_id=?",(stage.job_id,))
        assert cleanup.plan_retention(99999999999,is_all_writers_stopped=True).stage_ids==()


def test_aborted_unstarted_publication_can_release_its_sealed_stage(workspace):
    directory,_,_,sheets=workspace
    with closing(LocalPublicationSafety(directory/'state.sqlite')) as store, closing(LocalWorkbookStaging(directory/'staging.sqlite')) as staging:
        job=store.submit_job('aborted',TaskSpec('recompute_healthcheck','other','release'))
        result={'workbooks':{'book':{'score':50}},'layout_revisions':{'book':'layout'}}
        store.complete_compute(store.claim('worker','release'),result)
        claim=store.begin_publication(job.job_id,'publisher','release',('book',))
        stage,chunks=prepare_workbook_stage(store,sheets,claim,'book')
        staging.begin_stage(stage)
        for i,chunk in enumerate(chunks):staging.put_stage_chunk(stage.stage_id,i,chunk)
        staging.seal_stage(stage.stage_id)
        plan=prepare_publication_abort(store,sheets,claim.publication_id,'reconciler',True)
        store.commit_publication_abort('abort',plan,plan.snapshot_id,True)
    with closing(LocalStagingRetention(directory)) as cleanup:
        plan=cleanup.plan_retention(99999999999,is_all_writers_stopped=True)
        assert stage.stage_id in plan.stage_ids
        cleanup.apply_retention(plan,plan.plan_id,True)
        assert cleanup.store.get_job(job.job_id).state=='failed'
        assert sheets.read('book')['generated']=={'score':100}


def test_limit_keeps_other_terminal_stages_and_shared_object(workspace):
    directory,stage,_,_=workspace
    with closing(LocalStagingRetention(directory)) as cleanup:
        sibling=replace(stage,base_commit_id='another-base')
        cleanup.staging.begin_stage(sibling)
        cleanup.staging.seal_stage(sibling.stage_id)
        plan=cleanup.plan_retention(99999999999,limit=1,is_all_writers_stopped=True)
        assert len(plan.stage_ids)==1 and plan.object_ids==()
        cleanup.apply_retention(plan,plan.plan_id,True)
        second=cleanup.plan_retention(99999999999,limit=1,is_all_writers_stopped=True)
        assert len(second.stage_ids)==1 and second.object_ids==(stage.manifest.object_id,)


def test_backup_keeps_cleanup_receipt_and_restore_cannot_initialize_writer(workspace,tmp_path):
    from adapters.local_backup import create_backup,restore_backup
    directory,stage,_,_=workspace
    with closing(LocalStagingRetention(directory)) as cleanup:
        plan=cleanup.plan_retention(99999999999,is_all_writers_stopped=True)
        receipt=cleanup.apply_retention(plan,plan.plan_id,True)
    archive=tmp_path/'backup.zip'
    backup=create_backup(directory,archive,'release')
    restored=tmp_path/'restored'
    restore_backup(archive,restored,backup['backup_id'],'release')
    before=(restored/'state.sqlite').read_bytes(),(restored/'staging.sqlite').read_bytes()
    with pytest.raises(ValueError,match='fenced'):LocalStagingRetention(restored)
    assert before==((restored/'state.sqlite').read_bytes(),(restored/'staging.sqlite').read_bytes())
    with sqlite3.connect((restored/'staging.sqlite').as_uri()+'?mode=ro',uri=True) as connection:
        assert receipt['receipt_id'] in connection.execute('SELECT receipt_json FROM staging_retention_receipts').fetchone()[0]


@pytest.mark.parametrize('limit',[True,0,101])
def test_invalid_limits_do_not_scan_inventory(workspace,limit):
    with closing(LocalStagingRetention(workspace[0])) as cleanup:
        with pytest.raises(ValueError):cleanup.plan_retention(99999999999,limit,is_all_writers_stopped=True)


def test_budget_failure_after_first_delete_rolls_back_everything(workspace,monkeypatch):
    directory,stage,_,_=workspace
    with closing(LocalStagingRetention(directory)) as cleanup:
        plan=cleanup.plan_retention(99999999999,is_all_writers_stopped=True)
        def check_after_delete():
            if not cleanup.staging.connection.execute('SELECT 1 FROM workbook_stages WHERE stage_id=?',(stage.stage_id,)).fetchone():
                raise ComputeLimit('injected_after_delete')
        monkeypatch.setattr('adapters.local_staging_retention.check_execution',check_after_delete)
        with pytest.raises(ComputeLimit,match='injected_after_delete'):cleanup.apply_retention(plan,plan.plan_id,True)
        assert cleanup.staging.state(stage.stage_id)=='activated'
        assert cleanup.staging.connection.execute('SELECT COUNT(*) FROM staging_retention_receipts').fetchone()[0]==0


def test_actual_cli_plan_confirm_replay_and_no_workbook_change(workspace,tmp_path):
    import json
    import subprocess
    import sys
    directory,stage,_,sheets=workspace
    before=sheets.read('book')
    plan_file=tmp_path/'plan.json'
    base=[sys.executable,'-m','executors.local.staging_retention']
    options=['--workspace',str(directory),'--plan',str(plan_file),'--all-mock-writers-stopped']
    planned=subprocess.run([*base,'plan',*options,'--cutoff','99999999999'],capture_output=True,text=True,timeout=30)
    assert planned.returncode==0,planned.stderr
    plan_id=json.loads(planned.stdout)['plan_id']
    overwritten=subprocess.run([*base,'plan',*options,'--cutoff','99999999999'],capture_output=True,text=True,timeout=30)
    assert overwritten.returncode==2
    bad=subprocess.run([*base,'apply',*options,'--confirm-plan','0'*64],capture_output=True,text=True,timeout=30)
    assert bad.returncode==2
    applied=subprocess.run([*base,'apply',*options,'--confirm-plan',plan_id],capture_output=True,text=True,timeout=30)
    assert applied.returncode==0,applied.stderr
    replay=subprocess.run([*base,'apply',*options,'--confirm-plan',plan_id],capture_output=True,text=True,timeout=30)
    assert replay.returncode==0 and json.loads(applied.stdout)==json.loads(replay.stdout)
    assert json.loads(applied.stdout)['stage_ids']==[stage.stage_id]
    assert sheets.read('book')==before
