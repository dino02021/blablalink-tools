"""Atomic source adoption queues only a fenced, immutable draft computation."""

from contextlib import closing
import sqlite3

import pytest

from adapters.local_raid_sources import LocalRaidSources
from application.tasks import StateConflict
from executors.local.worker import run_worker
from local_tests.test_raid_sources import registry, changed_candidate, prepared, compiled
from local_tests.test_raid_inputs import TIME


def adopt_with_draft(store,candidate,request='adopt-and-draft'):
    return store.adopt_raid_sources(request,candidate,candidate,'actor','mock-release-v1',is_generate_draft=True)


def test_adoption_and_draft_enqueue_are_atomic_and_receipt_replays(registry):
    directory,store,health,problem,confirmation,_,route,_,sheets,candidate=registry
    receipt=adopt_with_draft(store,candidate)
    job=store.get_job(receipt['followup']['job_id'])
    assert job.state=='queued' and job.spec.version_map['raid_source_bundle']==candidate
    assert receipt['followup']['problem_id']==problem.problem_id
    assert store.input_head('union_demo')['problem_id']==problem.problem_id
    assert store.confirmed_head('union_demo')==confirmation
    assert adopt_with_draft(store,candidate)==receipt
    assert sheets.read('raid_output')['generated']=={}


def test_actual_worker_computes_draft_without_confirming_or_publishing(registry):
    directory,store,health,problem,confirmation,_,_,_,sheets,candidate=registry
    receipt=adopt_with_draft(store,candidate)
    result=run_worker(directory,health.spec.code_sha,is_publish_enabled=True,max_workflow_checks=0)
    assert result['processed']==[{'job_id':receipt['followup']['job_id'],'command':'generate_raid_draft','state':'succeeded','error_code':None}]
    job=store.get_job(receipt['followup']['job_id'])
    computed=store.get_result(job.result_id)
    assert store.draft('union_demo',computed['draft_plan_id'])['problem_id']==problem.problem_id
    assert store.confirmed_head('union_demo')==confirmation
    assert sheets.read('raid_output')['generated']=={}


def test_changed_source_fences_old_draft_and_replay_never_rewinds_heads(registry):
    _,store,health,problem,_,_,route,_,_,candidate=registry
    first=adopt_with_draft(store,candidate)
    changed,_=changed_candidate(store,health,problem,route,'settings')
    second=adopt_with_draft(store,changed,'new-source')
    assert not store.is_job_current(first['followup']['job_id'])
    assert store.is_job_current(second['followup']['job_id'])
    assert adopt_with_draft(store,candidate,'old-replay')==first
    assert store.raid_source_version('union_demo')['bundle_id']==changed
    assert store.input_head('union_demo')['problem_id']==second['followup']['problem_id']
    lease=store.claim('worker',health.spec.code_sha,commands=('generate_raid_draft',))
    assert lease.job_id==second['followup']['job_id']


def test_aba_source_creates_new_job_even_when_problem_returns_to_old_id(registry):
    _,store,health,problem,_,_,route,_,_,candidate=registry
    first=adopt_with_draft(store,candidate)
    changed,_=changed_candidate(store,health,problem,route,'settings')
    second=adopt_with_draft(store,changed,'source-b')
    versions=dict(problem.input_versions)
    returned=store.create_raid_source_candidate(health.job_id,versions['raid_settings'],versions['raid_member_inputs'],route.snapshot_id,TIME,health.spec.code_sha)
    third=adopt_with_draft(store,returned,'source-a-again')
    assert first['followup']['problem_id']==third['followup']['problem_id']
    assert len({row['followup']['job_id'] for row in (first,second,third)})==3
    assert not store.is_job_current(first['followup']['job_id'])
    assert store.is_job_current(third['followup']['job_id'])


def test_enqueue_failure_rolls_back_source_problem_head_and_receipt(registry):
    _,store,health,problem,_,_,route,_,_,candidate=registry
    before_jobs=store.list_jobs()
    before_problem=store.input_head('union_demo')
    store._connection.execute("CREATE TRIGGER deny_followup BEFORE INSERT ON raid_source_followups BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(sqlite3.IntegrityError,match='injected'):adopt_with_draft(store,candidate)
    assert store.raid_source_version('union_demo') is None
    assert store.input_head('union_demo')==before_problem
    assert store.list_jobs()==before_jobs
    assert store._connection.execute('SELECT COUNT(*) FROM raid_source_adoptions').fetchone()[0]==0
    store._connection.execute('DROP TRIGGER deny_followup')
    assert adopt_with_draft(store,candidate)['followup']


def test_choice_is_part_of_adoption_intent_and_requires_boolean(registry):
    _,store,health,problem,_,_,route,_,_,candidate=registry
    with pytest.raises(ValueError):store.adopt_raid_sources('bad',candidate,candidate,'actor',health.spec.code_sha,is_generate_draft=1)
    receipt=store.adopt_raid_sources('adopt-only',candidate,candidate,'actor',health.spec.code_sha)
    assert 'followup' not in receipt
    with pytest.raises(StateConflict):adopt_with_draft(store,candidate,'adopt-only')
    assert store._connection.execute('SELECT COUNT(*) FROM raid_source_followups').fetchone()[0]==0


def test_route_only_adoption_keeps_existing_confirmation_but_fences_old_followup(registry):
    _,store,health,problem,confirmation,_,route,_,_,candidate=registry
    first=adopt_with_draft(store,candidate)
    before=store.confirmation_workbook(confirmation)
    changed,_=changed_candidate(store,health,problem,route,'route')
    second=adopt_with_draft(store,changed,'route')
    assert first['followup']['problem_id']==second['followup']['problem_id']
    assert not store.is_job_current(first['followup']['job_id'])
    assert not store.confirmation_status(confirmation)['is_stale']
    assert store.confirmation_workbook(confirmation)==before


def test_restart_and_corrupt_followup_receipt_are_checked(registry):
    directory,store,health,problem,_,_,route,_,_,candidate=registry
    receipt=adopt_with_draft(store,candidate)
    with closing(LocalRaidSources(directory/'state.sqlite')) as reopened:
        assert adopt_with_draft(reopened,candidate)==receipt
        reopened._connection.execute('DELETE FROM raid_source_followups WHERE adoption_id=?',(receipt['adoption_id'],))
        with pytest.raises(StateConflict,match='followup'):adopt_with_draft(reopened,candidate)


def test_native_child_runs_adopted_followup_without_any_sheet_publication(registry):
    from executors.local.isolated import run_isolated_worker
    directory,store,health,_,confirmation,_,_,_,sheets,candidate=registry
    receipt=adopt_with_draft(store,candidate)
    result=run_isolated_worker(directory,health.spec.code_sha,seconds=30)
    assert result['outcome']=='completed'
    assert result['child_result']['processed'][0]['job_id']==receipt['followup']['job_id']
    assert result['child_result']['processed'][0]['state']=='succeeded'
    assert store.confirmed_head('union_demo')==confirmation and sheets.read('raid_output')['generated']=={}


def test_budget_interruption_after_source_head_write_rolls_back_whole_action(registry):
    from application.compute_budget import ComputeLimit
    from domain.execution import execution_checks
    _,store,_,_,_,_,_,_,_,candidate=registry
    before_jobs=store.list_jobs()
    def stop_after_adoption():
        if store.raid_source_version('union_demo') is not None:raise ComputeLimit('injected_after_adopt')
    with pytest.raises(ComputeLimit,match='injected_after_adopt'):
        with execution_checks(stop_after_adoption):adopt_with_draft(store,candidate)
    assert store.raid_source_version('union_demo') is None and store.list_jobs()==before_jobs
    assert store._connection.execute('SELECT COUNT(*) FROM raid_source_adoptions').fetchone()[0]==0


def test_backup_and_retention_preserve_followup_binding_without_running_it(registry,tmp_path):
    from adapters.local_backup import create_backup,restore_backup
    from adapters.local_retention import LocalObjectRetention
    directory,store,_,_,_,_,_,_,_,candidate=registry
    receipt=adopt_with_draft(store,candidate)
    with closing(LocalObjectRetention(directory/'state.sqlite')) as retention:
        plan=retention.plan_retention(99999999999)
        assert receipt['followup']['problem_id'] not in plan.object_ids
    archive=tmp_path/'backup.zip'
    backup=create_backup(directory,archive,'mock-release-v1')
    restored=tmp_path/'restored'
    restore_backup(archive,restored,backup['backup_id'],'mock-release-v1')
    with sqlite3.connect((restored/'state.sqlite').as_uri()+'?mode=ro',uri=True) as connection:
        assert connection.execute('SELECT job_id,problem_id FROM raid_source_followups WHERE adoption_id=?',(receipt['adoption_id'],)).fetchone()==tuple(receipt['followup'][key] for key in ('job_id','problem_id'))
    assert (restored/'restore-lock.json').is_file()
