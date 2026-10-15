"""Same-fixture comparison of guard-batch and receipt-driven local D1 prototypes."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from application.tasks import StateConflict
from coordination.d1_cas_contract import HeadCasIntent,sum_d1_query_usage
from coordination.receipt_d1_contract import SCHEMA_STATEMENTS,SCHEMA_ID,build_receipt_cas_batch,verify_receipt_cas_result
from executors.local.d1_contract_runtime import run_d1_contract_runtime
from executors.local.emulator_process import run_d1_emulator


def run_receipt_d1_comparison():
    baseline=run_d1_contract_runtime()
    initial=HeadCasIntent('initial','mock_union','healthcheck',0,'a'*64,'mock-release','mock_actor')
    stale=HeadCasIntent('stale','mock_union','healthcheck',0,'b'*64,'mock-release','mock_actor')
    changed=HeadCasIntent('changed','mock_union','healthcheck',1,'b'*64,'mock-release','mock_actor')
    operations=[{'kind':'batch','statements':[asdict(statement) for statement in build_receipt_cas_batch(intent)]}
        for intent in (initial,stale,changed,initial)]
    operations.append({'kind':'query','sql':'SELECT version_id,revision,(SELECT COUNT(*) FROM contract_receipts) AS receipts FROM contract_heads','parameters':[]})
    supervision=run_d1_emulator({'schema_statements':SCHEMA_STATEMENTS,'operations':operations,'probe_network':True})
    if supervision['outcome']!='completed' or not supervision['is_child_stopped']:
        raise StateConflict('receipt-driven local runtime outcome is unproven')
    result=json.loads(supervision.pop('stdout'))
    if (result.get('is_mock') is not True or result.get('network_providers')!=[] or result.get('are_sdk_children_closed') is not True
            or result.get('is_environment_sanitized') is not True or result.get('is_network_blocked') is not True
            or result.get('sdk_child_count',0)<1 or len(result.get('operations',[]))!=5
            or result.get('miniflare_version')!=baseline['miniflare_version'] or result.get('workerd_version')!=baseline['workerd_version']):
        raise StateConflict('comparison runtime or fixture boundaries differ')
    observed=result['operations']
    for index,intent in ((0,initial),(2,changed),(3,initial)):
        if observed[index]['outcome']!='completed':raise StateConflict('receipt-driven CAS did not complete')
        verify_receipt_cas_result(intent,observed[index]['results'])
    if (observed[1]['outcome']!='rejected' or 'head_revision_conflict' not in observed[1].get('error_message','')
            or observed[4].get('result',{}).get('results')!=[{'version_id':'b'*64,'revision':2,'receipts':2}]):
        raise StateConflict('receipt-driven stale CAS or replay changed the latest head')
    usage={'new_cas':sum_d1_query_usage(observed[0]['results']),'batch_replay':sum_d1_query_usage(observed[3]['results'])}
    if (usage['new_cas']['rows_written']>=baseline['emulator_query_usage']['new_cas']['rows_written']
            or usage['batch_replay']['rows_written']!=0):
        raise StateConflict('receipt-driven local write reduction is unproven')
    return {'schema_version':1,'scope':'standalone_head_contracts','is_mock':True,'is_production_accepted':False,
        'network_providers':[],'is_comparison_passed':True,'is_complete_account_usage':False,'receipt_schema_id':SCHEMA_ID,
        'baseline_usage':baseline['emulator_query_usage'],'receipt_driven_usage':usage,
        'baseline_supervision':baseline['supervision'],'receipt_supervision':supervision,
        'baseline_details':baseline,'receipt_details':result}


def main():
    parser=argparse.ArgumentParser(description='Local D1 write-cost comparison; no account or remote state')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    try:
        if args.output and args.output.exists():raise ValueError('comparison evidence must be a new file')
        result=run_receipt_d1_comparison()
        if args.output:
            with args.output.open('x',encoding='utf-8') as output:output.write(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    except (ValueError,KeyError,TypeError,OSError,StateConflict) as error:
        parser.exit(2,f'local D1 comparison error: {type(error).__name__}\n')
    print(json.dumps(result,ensure_ascii=False))
    return 0


if __name__=='__main__':raise SystemExit(main())
