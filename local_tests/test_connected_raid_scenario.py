"""One connected synthetic flow across actual stores, native children and mock books."""

from contextlib import closing
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from adapters.local_raid_sources import LocalRaidSources
from adapters.local_system_status import read_system_status
from adapters.mock_sheets import MockSheets
from application.tasks import StateConflict
from executors.local.raid_scenario import CODE,HUMANS,RAID,SCOPE,STATUS,run_raid_scenario


def test_whole_raid_flow_preserves_pending_edits_and_adopted_publications(tmp_path):
    directory=tmp_path/'system'
    result=run_raid_scenario(directory)
    assert result['is_mock'] and result['is_adopted_work_settled'] and result['is_human_data_preserved']
    assert result['is_production_accepted'] is False and result['network_providers']==[]
    assert result['has_unadopted_human_edit'] and result['member_grid_id']!=result['pending_member_grid_id']
    assert result['published_families']==['healthcheck','raid_publication']
    assert result['raid_tab_count']==5 and result['status_tab_count']==3
    assert all(receipt['outcome']=='completed' and receipt['is_child_stopped'] and receipt['memory_limit_bytes']==512*1024*1024
        and receipt['network_providers']==[] for receipt in result['native_executions'])
    assert len(result['cleanup_receipt']['stage_ids'])==len(result['cleanup_receipt']['object_ids'])==2
    sheets=MockSheets(directory/'workbooks')
    with closing(LocalRaidSources(directory/'state.sqlite')) as store:
        assert store.confirmed_head(SCOPE)==result['confirmation_id']
        assert not store.confirmation_status(result['confirmation_id'])['is_stale']
        bundle=store.raid_source_candidate(result['source_candidate_id'])
        assert bundle.payload['member_grid_id']==result['member_grid_id']
        assert store.get_document(bundle.payload['member_inputs_id']).payload['members'][0]['fixed_inputs']['slots'][0]=='正五'
        assert not store.has_successful_publication(result['draft_job_id'])
        assert store.has_successful_publication(result['raid_publication_job_id'])
        assert store.has_successful_publication(result['status_job_id'])
    assert sheets.read(HUMANS)['inputs']['raid_member_grid']['rows'][0][2]=='負二'
    restored=directory/'restored'
    before=(restored/'state.sqlite').read_bytes()
    status=read_system_status(restored,(SCOPE,))
    assert status['is_restore_fenced'] and result['is_restore_fenced']
    restored_sheets=MockSheets(restored/'workbooks')
    assert all(restored_sheets.read(resource)==sheets.read(resource) for resource in (RAID,STATUS,HUMANS))
    assert (restored/'state.sqlite').read_bytes()==before


def test_actual_raid_scenario_cli_emits_same_new_file_evidence(tmp_path):
    directory=tmp_path/'cli-system'
    output=tmp_path/'evidence.json'
    env={key:os.environ[key] for key in ('PATH','SYSTEMROOT','WINDIR','TEMP','TMP') if key in os.environ}
    env.update({'PYTHONIOENCODING':'utf-8','PYTHONUTF8':'1'})
    process=subprocess.run([sys.executable,'-m','executors.local.raid_scenario','--workspace',str(directory),'--output',str(output)],
        capture_output=True,text=True,encoding='utf-8',env=env,timeout=30,cwd=Path(__file__).resolve().parents[1])
    assert process.returncode==0,process.stderr
    assert json.loads(process.stdout)==json.loads(output.read_text(encoding='utf-8'))
    assert json.loads(process.stdout)['is_adopted_work_settled']


def test_existing_workspace_is_never_reset_by_scenario(tmp_path):
    directory=tmp_path/'existing'
    directory.mkdir()
    sentinel=directory/'human.txt'
    sentinel.write_text('preserve',encoding='utf-8')
    with pytest.raises(ValueError,match='new workspace'):run_raid_scenario(directory)
    assert sentinel.read_text(encoding='utf-8')=='preserve'
    assert not (directory/'state.sqlite').exists()


def test_failed_native_draft_never_creates_confirmation_or_published_output(tmp_path,monkeypatch):
    monkeypatch.setattr('executors.local.raid_scenario.run_isolated_worker',lambda *args,**kwargs:{'outcome':'killed','is_child_stopped':True})
    directory=tmp_path/'failed-draft'
    with pytest.raises(StateConflict,match='native child'):run_raid_scenario(directory)
    with closing(LocalRaidSources(directory/'state.sqlite')) as store:
        assert store.confirmed_head(SCOPE) is None
        assert not any(job.spec.command=='publish_raid_plan' for job in store.list_jobs())
    assert MockSheets(directory/'workbooks').read(RAID)['generated']=={}


def test_status_child_failure_keeps_prior_confirmed_raid_success(tmp_path,monkeypatch):
    from executors.local.isolated import run_isolated_worker
    calls=[]
    def fail_second(*args,**kwargs):
        calls.append(True)
        return run_isolated_worker(*args,**kwargs) if len(calls)==1 else {'outcome':'killed','is_child_stopped':True}
    monkeypatch.setattr('executors.local.raid_scenario.run_isolated_worker',fail_second)
    directory=tmp_path/'failed-status'
    with pytest.raises(StateConflict,match='native child'):run_raid_scenario(directory)
    sheets=MockSheets(directory/'workbooks')
    assert sheets.read(RAID)['generated']['publication_kind']=='confirmed_raid'
    assert sheets.read(STATUS)['generated']=={}
    status=read_system_status(directory,(SCOPE,))
    assert {row['family'] for row in status['scopes'][0]['published_by_family']}=={'healthcheck','raid_publication'}
