"""Offline explicit plan/confirm interface for terminal staging transport cleanup."""

import argparse
from contextlib import closing
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3

from adapters.local_staging_retention import LocalStagingRetention, StagingRetentionPlan
from adapters.process_memory import local_compute_budget
from application.compute_budget import ComputeLimit
from application.tasks import ResourceBusy, StateConflict
from domain.execution import execution_checks
from domain.snapshots import canonical_json


def main():
    parser=argparse.ArgumentParser(description='Explicit local mock staging cleanup; no network or workbook writes')
    parser.add_argument('command',choices=('plan','apply'))
    parser.add_argument('--workspace',required=True,type=Path)
    parser.add_argument('--plan',required=True,type=Path)
    parser.add_argument('--cutoff',type=float)
    parser.add_argument('--limit',type=int,default=100)
    parser.add_argument('--confirm-plan')
    parser.add_argument('--all-mock-writers-stopped',action='store_true',required=True)
    args=parser.parse_args()
    try:
        with closing(LocalStagingRetention(args.workspace)) as cleanup, execution_checks(local_compute_budget().check):
            if args.command=='plan':
                if args.cutoff is None:raise ValueError('explicit cutoff required')
                plan=cleanup.plan_retention(args.cutoff,args.limit,args.all_mock_writers_stopped)
                with args.plan.open('x',encoding='utf-8') as output:output.write(canonical_json(asdict(plan))+'\n')
                result={'is_mock':True,'plan_id':plan.plan_id,'stage_count':len(plan.stage_ids),'chunk_bytes':plan.chunk_bytes}
            else:
                if args.plan.stat().st_size>65536:raise ValueError('bounded plan required')
                plan=StagingRetentionPlan(**json.loads(args.plan.read_text(encoding='utf-8')))
                result=cleanup.apply_retention(plan,args.confirm_plan,args.all_mock_writers_stopped)
    except (ValueError,TypeError,KeyError,OSError,StateConflict,ResourceBusy,ComputeLimit,sqlite3.Error) as error:
        parser.exit(2,f'mock staging retention error: {type(error).__name__}\n')
    print(json.dumps(result,ensure_ascii=False))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
