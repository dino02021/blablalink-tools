"""Connected synthetic raid collaboration flow; no formal providers or credentials."""

import argparse
from contextlib import closing
import json
from pathlib import Path

from adapters.legacy_raid_grid import prepare_legacy_raid_grid
from adapters.local_backup import create_backup,restore_backup
from adapters.local_raid_sources import LocalRaidSources
from adapters.local_staging_retention import LocalStagingRetention
from adapters.local_system_status import read_system_status
from adapters.local_worker_store import LocalWorkerStore
from adapters.mock_sheets import MockSheets
from application.raid_member_grid import capture_raid_member_grid
from application.raid_publication import compile_raid_publication
from application.system_status_publication import capture_system_status_route
from application.tasks import StateConflict
from domain.healthcheck import ELEMENTS
from domain.snapshots import MemberKey,capture_document
from executors.local.demo import run_demo
from executors.local.isolated import run_isolated_worker
from executors.local.worker import run_worker

CODE='mock-release-v1'
SCOPE='union_demo'
TIME='2026-10-16T00:00:00Z'
RAID='raid_integrated_mock'
STATUS='status_integrated_mock'
HUMANS='legacy_integrated_mock'


def run_raid_scenario(directory):
    directory=Path(directory).resolve()
    if directory.exists():raise ValueError('raid scenario requires a new workspace')
    initial=run_demo(directory)
    sheets=MockSheets(directory/'workbooks')
    sheets.create(RAID,'raid_layout',{'manual':'keep'})
    sheets.set_manual_tab(RAID,'notes','人工備註',[['=literal',0,'']])
    sheets.create(STATUS,'status_layout',{'manual':'keep'})
    with closing(LocalRaidSources(directory/'state.sqlite')) as store:
        health=store.get_job(initial['job_id'])
        inputs=store.get_document(health.spec.version_map['union_inputs'])
        members=[MemberKey(**row['member']).key for row in inputs.payload['members']]
        legacy={'legacy_raid_member_rows':[['same_legacy_name',100],['same_legacy_name',110]],
            'legacy_raid_plan_rows':[['same_legacy_name',*(['old_output']*14),'負一','','',0,'',None,12.5,20],
                ['same_legacy_name',*(['other_output']*14),'正五','','',0,'',None,12.5,20]],
            'legacy_raid_history':[],'legacy_raid_is_complete':True,'legacy_raid_formula_cells':[],
            'manual_note':'before migration'}
        sheets.create(HUMANS,'legacy_layout',legacy)
        plan=prepare_legacy_raid_grid(HUMANS,sheets.read(HUMANS),inputs,{2:members[0],3:members[1]},
            {3:members[1],4:members[0]},TIME,CODE)
        collaborator=sheets.read(HUMANS)['inputs']
        collaborator['manual_note']='collaborator changed before apply'
        sheets.edit_inputs(HUMANS,collaborator)
        sheets.apply_legacy_raid_grid(plan,inputs,plan.plan_id,CODE)
        raw=capture_raid_member_grid(inputs,TIME,sheets.read(HUMANS)['inputs']['raid_member_grid'])
        # A subsequent unadopted edit must survive every downstream computation.
        later=sheets.read(HUMANS)['inputs']
        later['raid_member_grid']['rows'][0][2]='負二'
        sheets.edit_inputs(HUMANS,later)
        settings=capture_document('union_inputs',SCOPE,TIME,inputs.envelope['reference_version'],
            {'input_kind':'raid_settings','raid_schema_version':1,'hp':[['1000000000000']*5 for _ in range(3)],
                'boss_elements':list(ELEMENTS),'base_sync':100,'base_damage_yi':['10']*5,
                'element_tie_order':list(ELEMENTS),'overflow_policy':'legacy_effective'})
        route=capture_document('union_inputs',SCOPE,TIME,inputs.envelope['reference_version'],
            {'input_kind':'raid_publication_route','resource':RAID,'layout_revision':'raid_layout'})
        store.put_document(settings)
        store.put_document(route)
        candidate=store.create_raid_source_candidate_from_grid(health.job_id,settings.snapshot_id,raw,route.snapshot_id,TIME,CODE)
        adoption=store.adopt_raid_sources('adopt-and-draft',candidate,candidate,'mock_maintainer',CODE,True)
        draft_job_id=adoption['followup']['job_id']
        _require(store.confirmed_head(SCOPE) is None and sheets.read(RAID)['generated']=={},'adoption must not confirm or publish')
    draft_execution=run_isolated_worker(directory,CODE,seconds=30)
    _require_child(draft_execution,draft_job_id,'succeeded')
    with closing(LocalRaidSources(directory/'state.sqlite')) as store:
        _require(store.confirmed_head(SCOPE) is None and sheets.read(RAID)['generated']=={},'draft computation must not confirm or publish')
        draft=store.get_result(store.get_job(draft_job_id).result_id)
        head=store.input_head(SCOPE)
        confirmation=store.confirm('explicit-confirm','mock-confirm-intent',SCOPE,draft['draft_plan_id'],'mock_maintainer',
            head['revision'],None,is_accept_warnings=True)
        publication=compile_raid_publication(store,confirmation,route.snapshot_id,CODE)
        publish_job=store.prepare_raid_publication('explicit-publish',publication,publication.candidate_id,'mock-publication-intent')
        confirmed=store.confirmation(confirmation)
        _require(confirmed['is_accept_warnings'] is True,'synthetic warnings require explicit confirmation')
    raid_compute=run_worker(directory,CODE,max_jobs=1,max_workflow_checks=0)
    _require(raid_compute['processed'][0]['state']=='computed' and sheets.read(RAID)['generated']=={},'compute-only raid output changed')
    raid_publish=run_worker(directory,CODE,max_jobs=1,is_publish_enabled=True,max_workflow_checks=0)
    _require(raid_publish['processed'][0]['state']=='succeeded','confirmed raid was not published')
    with closing(LocalWorkerStore(directory/'state.sqlite')) as store:
        status_route=capture_system_status_route(TIME,CODE,STATUS,'status_layout',(SCOPE,))
        store.adopt_system_status_route('status-route',status_route,status_route.snapshot_id,0,'mock_maintainer')
        status_job=store.observe_system_status(directory,CODE,TIME)
    status_execution=run_isolated_worker(directory,CODE,seconds=30)
    _require_child(status_execution,status_job.job_id,'computed')
    _require(sheets.read(STATUS)['generated']=={},'native status computation published unexpectedly')
    status_publish=run_worker(directory,CODE,max_jobs=1,is_publish_enabled=True,max_workflow_checks=0)
    _require(status_publish['processed'][0]['state']=='succeeded','status workbook was not published')
    settled=run_worker(directory,CODE,max_jobs=1,is_publish_enabled=True,max_workflow_checks=0)
    _require(settled['processed']==[] and settled['system_status_capture']['job_id']==status_job.job_id,'status publication feeds back into itself')
    before_cleanup={resource:sheets.read(resource) for resource in (RAID,STATUS,HUMANS)}
    with closing(LocalStagingRetention(directory)) as retention:
        cleanup=retention.plan_retention(99999999999,is_all_writers_stopped=True)
        _require(len(cleanup.stage_ids)==2,'raid and status transport should be terminal')
        cleanup_receipt=retention.apply_retention(cleanup,cleanup.plan_id,True)
    _require(all(sheets.read(resource)==book for resource,book in before_cleanup.items()),'transport cleanup changed workbooks')
    archive=directory/'scenario-backup.zip'
    backup=create_backup(directory,archive,CODE)
    restored=directory/'restored'
    restore_backup(archive,restored,backup['backup_id'],CODE)
    is_restore_fenced=_is_restored_worker_fenced(restored)
    _require(is_restore_fenced,'restored workspace was not fenced')
    status=read_system_status(directory,(SCOPE,))
    scope=status['scopes'][0]
    raid_book,status_book,human_book=(sheets.read(resource) for resource in (RAID,STATUS,HUMANS))
    preserved=(human_book['inputs']['manual_note']=='collaborator changed before apply'
        and human_book['inputs']['raid_member_grid']['rows'][0][2]=='負二'
        and raid_book['inputs']=={'manual':'keep'} and raid_book['manual_tabs']['notes']['cells'][0][0]['value']=={'stringValue':'=literal'})
    _require(preserved and scope['is_all_desired_completed'] and not status['global']['is_write_blocked'],'connected scenario did not settle')
    _require(len(raid_book['generated_grid']['tabs'])==5 and len(status_book['generated_grid']['tabs'])==3,'typed workbook counts differ')
    pending_grid=capture_raid_member_grid(inputs,TIME,human_book['inputs']['raid_member_grid'])
    _require(pending_grid.snapshot_id!=raw.snapshot_id,'later human edit was accidentally adopted')
    return {'schema_version':1,'is_mock':True,'is_production_accepted':False,'network_providers':[],'code_sha':CODE,
        'scope_id':SCOPE,'is_adopted_work_settled':True,'has_unadopted_human_edit':True,'is_human_data_preserved':preserved,
        'pending_member_grid_id':pending_grid.snapshot_id,
        'legacy_migration_plan_id':plan.plan_id,'member_grid_id':raw.snapshot_id,'source_candidate_id':candidate,
        'draft_job_id':draft_job_id,'draft_plan_id':draft['draft_plan_id'],'confirmation_id':confirmation,
        'raid_publication_job_id':publish_job.job_id,'raid_commit_id':raid_book['commit_id'],
        'status_job_id':status_job.job_id,'status_commit_id':status_book['commit_id'],'status_hash':status['status_hash'],
        'published_families':[row['family'] for row in scope['published_by_family']],
        'raid_tab_count':5,'status_tab_count':3,'native_executions':[draft_execution,status_execution],
        'cleanup_receipt':cleanup_receipt,'backup_id':backup['backup_id'],'is_restore_fenced':is_restore_fenced}


def main():
    parser=argparse.ArgumentParser(description='Connected local mock raid scenario; no real provider access')
    parser.add_argument('--workspace',required=True,type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    try:
        if args.output and args.output.exists():raise ValueError('scenario output must be a new file')
        result=run_raid_scenario(args.workspace)
        if args.output:
            with args.output.open('x',encoding='utf-8') as target:target.write(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    except (ValueError,KeyError,TypeError,OSError,StateConflict) as error:
        parser.exit(2,f'mock raid scenario error: {type(error).__name__}\n')
    print(json.dumps(result,ensure_ascii=False))
    return 0


def _require_child(receipt,job_id,state):
    _require(receipt['outcome']=='completed' and receipt['is_child_stopped'],'native child did not finish safely')
    processed=receipt['child_result']['processed']
    _require(len(processed)==1 and processed[0]['job_id']==job_id and processed[0]['state']==state,'native computation differs')


def _is_restored_worker_fenced(directory):
    try:run_worker(directory,CODE,max_jobs=1,max_workflow_checks=0)
    except ValueError as error:return 'fenced' in str(error)
    return False


def _require(condition,message):
    if not condition:raise StateConflict(message)


if __name__=='__main__':raise SystemExit(main())
