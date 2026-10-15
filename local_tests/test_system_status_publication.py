"""Synthetic shared-status route, immutable snapshots, and actual mock publication."""

import pytest

from adapters.local_worker_store import LocalWorkerStore
from adapters.mock_sheets import MockSheets
from application.compute_budget import ComputeBudget, ComputeLimit
from application.system_status_publication import capture_system_status_route, render_system_status_workbook, run_system_status_job
from application.tasks import StateConflict, TaskSpec, ResourceBusy
from executors.local.demo import run_demo
from executors.local.worker import run_worker

NOW='2026-10-16T00:00:00Z'
CODE='mock-release-v1'


def setup_status(tmp_path, scopes=('union_demo',)):
    directory=tmp_path/'workspace'
    run_demo(directory)
    sheets=MockSheets(directory/'workbooks')
    sheets.create('status_book','status_layout',{'human':'keep'})
    sheets.set_manual_tab('status_book','notes','Notes',[['keep']])
    store=LocalWorkerStore(directory/'state.sqlite')
    route=capture_system_status_route(NOW,CODE,'status_book','status_layout',scopes)
    store.adopt_system_status_route('route',route,route.snapshot_id,0,'owner')
    return directory,store,sheets,route


def test_route_replay_cas_and_owner_separation(tmp_path):
    directory,store,sheets,route=setup_status(tmp_path)
    try:
        second=capture_system_status_route(NOW,CODE,'other_book','layout',('union_demo',))
        receipt=store.adopt_system_status_route('route2',second,second.snapshot_id,1,'owner')
        assert receipt['revision']==2
        store.adopt_system_status_route('route',route,route.snapshot_id,0,'owner')
        assert store.system_status_route()['route_id']==second.snapshot_id
        with pytest.raises(StateConflict):store.adopt_system_status_route('late',route,route.snapshot_id,0,'another-owner')
    finally:store.close()


@pytest.mark.parametrize('scopes',[('system_status',),'union_demo',(),('x','x'),tuple('scope'+str(i) for i in range(101))])
def test_invalid_route_scopes(scopes):
    with pytest.raises(ValueError):capture_system_status_route(NOW,CODE,'book','layout',scopes)


def test_observation_clock_dedup_and_aba_occurrence(tmp_path):
    directory,store,sheets,route=setup_status(tmp_path)
    try:
        first=store.observe_system_status(directory,CODE,NOW)
        same=store.observe_system_status(directory,CODE,'2026-10-16T00:01:00Z')
        assert same.job_id==first.job_id
        # Return to precisely the same source status without deleting immutable history.
        store._connection.execute("INSERT INTO usage_budget VALUES('mock','period',10,0,0)")
        second=store.observe_system_status(directory,CODE,'2026-10-16T00:02:00Z')
        store._connection.execute("DELETE FROM usage_budget WHERE platform='mock'")
        third=store.observe_system_status(directory,CODE,'2026-10-16T00:03:00Z')
        assert len({first.job_id,second.job_id,third.job_id})==3
        a=store.get_document(first.spec.version_map['status_occurrence'])
        c=store.get_document(third.spec.version_map['status_occurrence'])
        assert a.payload['observation_id']==c.payload['observation_id']
        assert c.payload['revision']==3
        assert not store.is_job_current(first.job_id)
        assert store.is_job_current(third.job_id)
    finally:store.close()


def test_worker_compute_publish_three_tabs_and_no_feedback(tmp_path):
    directory,store,sheets,route=setup_status(tmp_path)
    try:job=store.observe_system_status(directory,CODE,NOW)
    finally:store.close()
    computed=run_worker(directory,CODE,max_workflow_checks=0)
    assert computed['processed'][0]['state']=='computed'
    assert sheets.read('status_book')['generated']=={}
    published=run_worker(directory,CODE,is_publish_enabled=True,max_workflow_checks=0)
    assert published['processed'][0]['state']=='succeeded'
    book=sheets.read('status_book')
    assert book['inputs']=={'human':'keep'} and book['manual_tabs']['notes']['cells'][0][0]['value']['stringValue']=='keep'
    assert len(book['generated']['status_workbook_view']['tabs'])==3
    assert run_worker(directory,CODE,is_publish_enabled=True,max_workflow_checks=0)['processed']==[]
    assert published['system_status_capture']['job_id']==job.job_id


def test_worker_end_hook_queues_only_then_next_run_computes(tmp_path):
    directory,store,sheets,route=setup_status(tmp_path)
    store.close()
    first=run_worker(directory,CODE,max_workflow_checks=0)
    assert first['processed']==[] and first['system_status_capture']['state']=='queued'
    second=run_worker(directory,CODE,max_workflow_checks=0)
    assert [job['command'] for job in second['processed']]==['publish_system_status']


def test_hundred_scopes_stable_two_pages(tmp_path):
    scopes=tuple(f'union_{i:03}' for i in range(100))
    directory,store,sheets,route=setup_status(tmp_path,scopes)
    try:
        job=store.observe_system_status(directory,CODE,NOW)
        occurrence=store.get_document(job.spec.version_map['status_occurrence'])
        observation=store.get_document(occurrence.payload['observation_id'])
        view=render_system_status_workbook(route,occurrence,observation)
        assert [len(tab.rows) for tab in view.tabs]==[51,51,6]
        assert all(tab.is_generated_protected for tab in view.tabs)
    finally:store.close()


def test_route_change_fences_computed_job(tmp_path):
    directory,store,sheets,route=setup_status(tmp_path)
    try:
        job=store.observe_system_status(directory,CODE,NOW)
        lease=store.claim('worker',CODE,commands=('publish_system_status',))
        computed=run_system_status_job(store,lease,ComputeBudget())
        assert computed.state=='computed'
        disabled=capture_system_status_route(NOW,CODE,'status_book','status_layout',('union_demo',),False)
        store.adopt_system_status_route('disable',disabled,disabled.snapshot_id,1,'owner')
        assert not store.is_job_current(job.job_id)
        assert store.begin_publication(job.job_id,'publisher',CODE,('status_book',)) is None
        assert store.observe_system_status(directory,CODE,NOW) is None
    finally:store.close()


def test_budget_defers_without_result(tmp_path):
    directory,store,sheets,route=setup_status(tmp_path)
    try:
        job=store.observe_system_status(directory,CODE,NOW)
        lease=store.claim('worker',CODE,commands=('publish_system_status',))
        result=run_system_status_job(store,lease,ComputeBudget(max_checks=1))
        assert result.state=='capacity_blocked' and result.result_id is None
    finally:store.close()


def test_snapshot_binding_tamper_rejected(tmp_path):
    directory,store,sheets,route=setup_status(tmp_path)
    try:
        job=store.observe_system_status(directory,CODE,NOW)
        occurrence=store.get_document(job.spec.version_map['status_occurrence'])
        other=capture_system_status_route(NOW,CODE,'other','layout',('union_demo',))
        observation=store.get_document(occurrence.payload['observation_id'])
        with pytest.raises(StateConflict):render_system_status_workbook(other,occurrence,observation)
        assert store.observe_system_status(directory,'another-release',NOW) is None
    finally:store.close()


def test_unknown_publisher_blocks_new_observation_and_route_change(tmp_path):
    directory,store,sheets,route=setup_status(tmp_path)
    try:
        job=store.observe_system_status(directory,CODE,NOW)
        lease=store.claim('worker',CODE,commands=('publish_system_status',))
        run_system_status_job(store,lease,ComputeBudget())
        claim=store.begin_publication(job.job_id,'publisher',CODE,('status_book',))
        store.mark_publication_uncertain(claim)
        with pytest.raises(ResourceBusy):store.observe_system_status(directory,CODE,NOW)
        changed=capture_system_status_route(NOW,CODE,'other','layout',('union_demo',))
        with pytest.raises(ResourceBusy):store.adopt_system_status_route('changed',changed,changed.snapshot_id,1,'owner')
        assert store._connection.execute('SELECT COUNT(*) FROM system_status_occurrences').fetchone()[0]==1
    finally:store.close()
    result=run_worker(directory,CODE,max_workflow_checks=0)
    assert result['system_status_capture']['error_code']=='publisher_busy'
    assert sheets.read('status_book')['generated']=={}


def test_corrupt_watched_scope_does_not_hide_other_scopes(tmp_path):
    directory,store,sheets,route=setup_status(tmp_path,('union_demo','healthy_scope'))
    try:
        store._connection.execute("UPDATE jobs SET spec_json='{}' WHERE scope_id='union_demo'")
        job=store.observe_system_status(directory,CODE,NOW)
        occurrence=store.get_document(job.spec.version_map['status_occurrence'])
        observation=store.get_document(occurrence.payload['observation_id'])
        rows=observation.payload['status']['scopes']
        assert [(row['scope_id'],row['is_available']) for row in rows]==[('healthy_scope',True),('union_demo',False)]
        view=render_system_status_workbook(route,occurrence,observation)
        assert view.tabs[0].rows[-1][-1].value=='status_input_invalid'
    finally:store.close()


def test_capture_budget_rolls_back_all_versions(tmp_path):
    from domain.execution import execution_checks
    directory,store,sheets,route=setup_status(tmp_path)
    try:
        with pytest.raises(ComputeLimit,match='compute_operation_limit'):
            with execution_checks(ComputeBudget(max_checks=1).check):store.observe_system_status(directory,CODE,NOW)
        assert store._connection.execute('SELECT COUNT(*) FROM system_status_observations').fetchone()[0]==0
        assert store._connection.execute('SELECT COUNT(*) FROM system_status_occurrences').fetchone()[0]==0
        assert store.observe_system_status(directory,CODE,NOW).state=='queued'
    finally:store.close()


def test_native_child_computes_frozen_status_without_publication(tmp_path):
    from executors.local.isolated import run_isolated_worker
    directory,store,sheets,route=setup_status(tmp_path)
    try:store.observe_system_status(directory,CODE,NOW)
    finally:store.close()
    result=run_isolated_worker(directory,CODE,seconds=30)
    assert result['outcome']=='completed'
    assert result['child_result']['processed'][0]['state']=='computed'
    assert sheets.read('status_book')['generated']=={}


def test_backup_retains_status_versions_and_fences_new_capture(tmp_path):
    from adapters.local_backup import create_backup, restore_backup
    from adapters.local_retention import LocalObjectRetention
    from adapters.local_system_status import read_system_status
    directory,store,sheets,route=setup_status(tmp_path)
    try:
        job=store.observe_system_status(directory,CODE,NOW)
        occurrence=store.get_document(job.spec.version_map['status_occurrence'])
    finally:store.close()
    retention=LocalObjectRetention(directory/'state.sqlite')
    try:
        plan=retention.plan_retention(99999999999)
        assert not {route.snapshot_id,occurrence.snapshot_id,occurrence.payload['observation_id']}&set(plan.object_ids)
    finally:retention.close()
    archive=tmp_path/'backup.zip'
    backup=create_backup(directory,archive,CODE)
    restored=tmp_path/'restored'
    restore_backup(archive,restored,backup['backup_id'],CODE)
    assert read_system_status(restored,('union_demo',))['is_restore_fenced']
    import sqlite3
    restored_store=LocalWorkerStore.__new__(LocalWorkerStore)
    restored_store._connection=sqlite3.connect((restored/'state.sqlite').as_uri()+'?mode=ro',uri=True)
    restored_store._connection.row_factory=sqlite3.Row
    try:
        assert restored_store.get_document(occurrence.snapshot_id)==occurrence
        with pytest.raises(ValueError,match='fenced'):restored_store.observe_system_status(restored,CODE,NOW)
    finally:restored_store.close()
