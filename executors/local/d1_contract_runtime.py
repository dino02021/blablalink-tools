"""Actual local Miniflare binding proof for the independent atomic CAS contract."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from application.tasks import StateConflict
from coordination.d1_cas_contract import HeadCasIntent,build_head_cas_batch,verify_head_cas_batch_result,build_head_cas_lookup,verify_head_cas_lookup_result
from executors.local.emulator_process import run_d1_emulator


def run_d1_contract_runtime():
    initial=HeadCasIntent('initial','mock_union','healthcheck',0,'a'*64,'mock-release','mock_actor')
    stale=HeadCasIntent('stale','mock_union','healthcheck',0,'b'*64,'mock-release','mock_actor')
    changed=HeadCasIntent('changed','mock_union','healthcheck',1,'b'*64,'mock-release','mock_actor')
    operations=[{'kind':'batch','statements':[asdict(statement) for statement in build_head_cas_batch(intent)]}
        for intent in (initial,stale,changed,initial)]
    operations.append({'kind':'query','sql':'SELECT version_id,revision,(SELECT COUNT(*) FROM contract_receipts) AS receipt_count,'
        '(SELECT COUNT(*) FROM contract_cas_guards) AS guard_count FROM contract_heads','parameters':[]})
    operations.append({'kind':'lookup_or_batch','lookup':asdict(build_head_cas_lookup(initial)),'receipt':initial.receipt,
        'statements':[asdict(statement) for statement in build_head_cas_batch(initial)]})
    schema=(Path(__file__).resolve().parents[2]/'coordination/d1_contracts/0001_cas_contract.sql').read_text(encoding='utf-8')
    receipt=run_d1_emulator({'schema':schema,'operations':operations,'probe_network':True})
    if receipt['outcome']!='completed' or not receipt['is_child_stopped']:
        raise StateConflict('local D1 runtime outcome is unproven')
    result=json.loads(receipt.pop('stdout'))
    if (result.get('is_mock') is not True or result.get('network_providers')!=[] or result.get('are_sdk_children_closed') is not True
            or result.get('is_network_blocked') is not True or result.get('is_environment_sanitized') is not True
            or result.get('sdk_child_count',0)<1 or len(result.get('operations',[]))!=6):
        raise StateConflict('local emulator profile or result differs')
    observed=result['operations']
    for index,intent in ((0,initial),(2,changed),(3,initial)):
        if observed[index]['outcome']!='completed':raise StateConflict('expected local D1 CAS did not complete')
        verify_head_cas_batch_result(intent,observed[index]['results'])
    if (observed[1]['outcome']!='rejected' or 'CHECK constraint failed' not in observed[1].get('error_message','')
            or observed[4].get('result',{}).get('results')!=[{'version_id':'b'*64,'revision':2,'receipt_count':2,'guard_count':0}]):
        raise StateConflict('D1 stale CAS rollback or replay changed the latest head')
    if observed[5].get('mode')!='lookup_replay' or verify_head_cas_lookup_result(initial,observed[5].get('lookup_result'))!=initial.receipt:
        raise StateConflict('D1 positive receipt lookup did not preserve original decision')
    usage={'new_cas':_usage(observed[0]['results']),'batch_replay':_usage(observed[3]['results']),
        'verified_lookup':_usage([observed[5]['lookup_result']])}
    if usage['batch_replay']['rows_written']<=0 or usage['verified_lookup']['rows_written']!=0:
        raise StateConflict('D1 replay write-cost evidence differs')
    return {'schema_version':1,'is_mock':True,'is_production_accepted':False,'network_providers':[],
        'is_local_d1_contract_passed':True,'supervision':receipt,'emulator_query_usage':usage,'is_complete_account_usage':False,**result}


def main():
    parser=argparse.ArgumentParser(description='Local-only Miniflare D1 CAS proof; no account or remote bindings')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    try:
        if args.output and args.output.exists():raise ValueError('evidence output must be a new file')
        result=run_d1_contract_runtime()
        if args.output:
            with args.output.open('x',encoding='utf-8') as output:output.write(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    except (ValueError,KeyError,TypeError,OSError,StateConflict) as error:
        parser.exit(2,f'local D1 contract error: {type(error).__name__}\n')
    print(json.dumps(result,ensure_ascii=False))
    return 0


def _usage(results):
    counts={'rows_read':0,'rows_written':0}
    for result in results:
        for field in counts:
            value=result.get('meta',{}).get(field)
            if type(value) is not int or value<0:raise StateConflict('D1 local usage metadata is incomplete')
            counts[field]+=value
    return counts


if __name__=='__main__':raise SystemExit(main())
