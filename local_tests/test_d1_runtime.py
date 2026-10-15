"""Actual Miniflare/workerd tests in an owned Windows-only development process tree."""

from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import time

import pytest

from adapters.windows_child_job import WindowsChildJob,_api
from coordination.d1_cas_contract import HeadCasIntent,build_head_cas_batch,build_head_cas_lookup,verify_head_cas_lookup_result
from executors.local.d1_contract_runtime import run_d1_contract_runtime
from executors.local.emulator_process import run_d1_emulator
from executors.local.process_guard import run_python_module

pytestmark=pytest.mark.skipif(sys.platform!='win32',reason='Windows-owned emulator process profile requires actual Windows verification')
ROOT=Path(__file__).resolve().parents[1]


def payload(operations=(),**extra):
    return {'schema':(ROOT/'coordination/d1_contracts/0001_cas_contract.sql').read_text(encoding='utf-8'),
        'operations':list(operations),**extra}


def operation(request='request',revision=0,version='a'):
    intent=HeadCasIntent(request,'mock_union','healthcheck',revision,version*64,'mock-release','actor')
    return {'kind':'batch','statements':[asdict(statement) for statement in build_head_cas_batch(intent)]}


def test_actual_miniflare_cas_replay_barrier_network_and_children(monkeypatch):
    for key in ('CLOUDFLARE_API_TOKEN','DISCORD_TOKEN','GOOGLE_APPLICATION_CREDENTIALS','NODE_OPTIONS'):
        monkeypatch.setenv(key,'must-not-reach-sdk')
    report=run_d1_contract_runtime()
    assert report['is_local_d1_contract_passed'] and report['is_production_accepted'] is False
    assert report['miniflare_version']=='4.20260730.0' and report['workerd_version']=='1.20260730.1'
    assert report['compatibility_date']=='2026-07-30'
    assert report['sdk_child_count']>=1 and report['are_sdk_children_closed']
    assert report['blocked_outbound_attempts']==1 and report['is_network_blocked'] and report['network_providers']==[]
    assert report['supervision']['max_processes']==4 and report['supervision']['job_memory_limit_bytes']==512*1024*1024
    assert report['supervision']['is_child_stopped'] and 'stdout' not in report['supervision']
    assert report['emulator_query_usage']['batch_replay']['rows_written']>0
    assert report['emulator_query_usage']['verified_lookup']['rows_written']==0
    assert report['is_complete_account_usage'] is False


def test_runtime_batch_failure_rolls_back_prior_statement_before_followup_query():
    failed=operation()
    failed['statements'].insert(3,{'sql':"INSERT INTO contract_cas_guards VALUES('injected','signature',0)",'parameters':[]})
    result=run_d1_emulator(payload((failed,{'kind':'query','sql':'SELECT (SELECT COUNT(*) FROM contract_heads) AS heads,'
        '(SELECT COUNT(*) FROM contract_receipts) AS receipts,(SELECT COUNT(*) FROM contract_cas_guards) AS guards','parameters':[]})))
    assert result['outcome']=='completed' and result['is_child_stopped']
    observed=json.loads(result['stdout'])
    assert observed['operations'][0]['outcome']=='rejected'
    assert observed['operations'][1]['result']['results']==[{'heads':0,'receipts':0,'guards':0}]
    assert observed['are_sdk_children_closed']


def test_scope_barrier_is_evaluated_inside_the_actual_d1_batch():
    setup={'kind':'query','sql':'INSERT INTO contract_barriers VALUES(?,1)','parameters':['mock_union']}
    result=run_d1_emulator(payload((setup,operation(),{'kind':'query','sql':'SELECT COUNT(*) AS count FROM contract_heads','parameters':[]})))
    observed=json.loads(result['stdout'])
    assert result['outcome']=='completed' and observed['operations'][1]['outcome']=='rejected'
    assert observed['operations'][2]['result']['results']==[{'count':0}]


def test_deadline_closes_the_actual_sdk_child_tree_after_runtime_ready():
    result=run_d1_emulator(payload(hold_after_ready=True),seconds=3)
    assert result['outcome']=='deadline_terminated' and result['is_child_stopped']
    ready=json.loads(result['stdout'])
    assert ready['is_runtime_ready'] and ready['child_pids']
    deadline=time.monotonic()+2
    while not all(is_stopped(pid) for pid in ready['child_pids']) and time.monotonic()<deadline:time.sleep(.01)
    assert all(is_stopped(pid) for pid in ready['child_pids'])


def test_single_python_profile_still_cannot_spawn_a_second_process():
    statement="import subprocess,sys; subprocess.run([sys.executable,'-c','pass'],check=True)"
    result=run_python_module('timeit',('-n','1','-r','1',statement),seconds=5)
    assert result['outcome']=='child_failed' and result['is_child_stopped']


@pytest.mark.parametrize('maximum',[1,4])
def test_windows_kernel_reports_requested_active_and_aggregate_memory_limits(maximum):
    import ctypes
    job=WindowsChildJob(512*1024*1024,max_processes=maximum)
    try:
        kernel,kind=_api()
        query=kernel.QueryInformationJobObject
        query.argtypes=(ctypes.c_void_p,ctypes.c_int,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_void_p)
        from ctypes import wintypes
        query.restype=wintypes.BOOL
        limits=kind()
        assert query(job.handle,9,ctypes.byref(limits),ctypes.sizeof(limits),None)
        assert limits.BasicLimitInformation.ActiveProcessLimit==maximum
        assert limits.ProcessMemoryLimit==512*1024*1024
        assert bool(limits.BasicLimitInformation.LimitFlags&0x200)==(maximum>1)
        if maximum>1:assert limits.JobMemoryLimit==512*1024*1024
    finally:job.close()


@pytest.mark.parametrize('maximum',[0,5,True])
def test_unbounded_process_profiles_are_rejected_before_creating_a_job(maximum):
    with pytest.raises(ValueError):WindowsChildJob(512*1024*1024,max_processes=maximum)


@pytest.mark.parametrize('same_request',[False,True])
def test_actual_d1_concurrent_batches_keep_one_head_and_one_receipt(same_request):
    first=operation('first')
    second=first if same_request else operation('second',version='b')
    result=run_d1_emulator(payload(({'kind':'parallel_batches','batches':[first['statements'],second['statements']]},
        {'kind':'query','sql':'SELECT revision,(SELECT COUNT(*) FROM contract_receipts) AS receipts FROM contract_heads','parameters':[]})))
    observed=json.loads(result['stdout'])
    assert result['outcome']=='completed'
    assert sum(item['outcome']=='completed' for item in observed['operations'][0]['batches'])==(2 if same_request else 1)
    assert observed['operations'][1]['result']['results']==[{'revision':1,'receipts':1}]


def test_verified_receipt_lookup_avoids_guard_writes_on_normal_replay():
    intent=HeadCasIntent('lookup','mock_union','healthcheck',0,'a'*64,'mock-release','actor')
    lookup={'kind':'lookup_or_batch','lookup':asdict(build_head_cas_lookup(intent)),'receipt':intent.receipt,
        'statements':[asdict(statement) for statement in build_head_cas_batch(intent)]}
    result=run_d1_emulator(payload((lookup,lookup,operation('new',1,'b'),lookup)))
    observed=json.loads(result['stdout'])
    assert result['outcome']=='completed'
    assert [item['mode'] for item in (observed['operations'][0],observed['operations'][1],observed['operations'][3])]==['batch','lookup_replay','lookup_replay']
    for index in (1,3):
        item=observed['operations'][index]
        assert verify_head_cas_lookup_result(intent,item['lookup_result'])==intent.receipt
        assert item['lookup_result']['meta']['rows_written']==0
    assert sum(row['meta']['rows_written'] for row in observed['operations'][0]['results'])>0


def test_always_running_the_batch_on_replay_has_real_guard_write_cost():
    first=operation()
    result=run_d1_emulator(payload((first,first)))
    observed=json.loads(result['stdout'])
    assert result['outcome']=='completed'
    assert sum(row['meta']['rows_written'] for row in observed['operations'][1]['results'])>0


def is_stopped(pid):
    # Read-only PID observation; termination uses only the owned Job Object.
    import ctypes
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=(wintypes.DWORD,wintypes.BOOL,wintypes.DWORD)
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes=(wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD))
    kernel.GetExitCodeProcess.restype=wintypes.BOOL
    kernel.CloseHandle.argtypes=(wintypes.HANDLE,)
    handle=kernel.OpenProcess(0x1000,False,pid)
    if not handle:return ctypes.get_last_error()==87
    try:
        code=wintypes.DWORD()
        return bool(kernel.GetExitCodeProcess(handle,ctypes.byref(code))) and code.value!=259
    finally:kernel.CloseHandle(handle)
