"""One bounded fair workflow tick pass. No provider calls, publications or daemon."""

import argparse
import json
import sqlite3

from adapters.local_healthcheck_workflows import LocalHealthcheckWorkflows
from adapters.mock_workspace import require_mock_workspace
from adapters.process_memory import local_compute_budget
from application.compute_budget import ComputeLimit
from application.tasks import StateConflict, validate_identifier
from domain.execution import execution_checks


def tick_workflow_scopes(store, code_sha, max_scopes, budget):
    validate_identifier(code_sha)
    if type(max_scopes) is not int or not 1<=max_scopes<=100:
        raise ValueError('bounded workflow scope visits required')
    selected, processed, is_limited = (), [], False
    try:
        with execution_checks(budget.check):
            budget.check()
            selected = store.take_workflow_scopes(code_sha,max_scopes)
            for scope in selected:
                try:
                    result = store.reconcile_healthcheck_workflow(scope,code_sha,budget)
                    processed.append({'scope_id':scope,'state_revision':result['revision'],'status':result['state']['status'],
                        'error_code':result['state']['error_code']})
                except (StateConflict,ValueError,KeyError,TypeError,sqlite3.Error):
                    processed.append({'scope_id':scope,'status':'invalid','error_code':'workflow_input_invalid'})
    except ComputeLimit:
        is_limited = True
    return {'selected_count':len(selected),'processed':processed,'is_budget_limited':is_limited}


def run_workflow_ticks(directory, code_sha, max_scopes=10, budget_factory=local_compute_budget):
    validate_identifier(code_sha)
    if len(code_sha)>128 or type(max_scopes) is not int or not 1<=max_scopes<=100:
        raise ValueError('bounded workflow invocation required')
    directory = require_mock_workspace(directory,is_writer=True)
    store = LocalHealthcheckWorkflows(directory/'state.sqlite')
    try:
        result = tick_workflow_scopes(store,code_sha,max_scopes,budget_factory())
        return {'is_mock':True,**result,'network_providers':[]}
    finally:
        store.close()


class WorkflowTickSession:
    def __init__(self, store, code_sha, max_checks, budget):
        if type(max_checks) is not int or not 0<=max_checks<=100:
            raise ValueError('bounded workflow visit count required')
        self.store,self.code_sha,self.budget = store,code_sha,budget
        self.remaining,self.selected = max_checks,0
        self.latest,self.is_limited,self.error_code = {},False,None

    def advance(self):
        if not self.remaining or self.is_limited or self.error_code:
            return
        try:
            result = tick_workflow_scopes(self.store,self.code_sha,min(10,self.remaining),self.budget)
        except (StateConflict,ValueError,KeyError,TypeError,sqlite3.Error):
            self.error_code = 'workflow_cursor_invalid'
            return
        self.remaining -= result['selected_count']
        self.selected += result['selected_count']
        self.is_limited = result['is_budget_limited']
        for item in result['processed']:
            self.latest[item['scope_id']] = item

    def summary(self):
        return {'workflow_ticks':[self.latest[scope] for scope in sorted(self.latest)],'workflow_scopes_selected':self.selected,
            'is_workflow_budget_limited':self.is_limited,'workflow_error_code':self.error_code}


def main():
    parser = argparse.ArgumentParser(description='One bounded mock workflow tick pass; never publishes')
    parser.add_argument('--workspace',required=True)
    parser.add_argument('--code-sha',required=True)
    parser.add_argument('--max-scopes',type=int,default=10)
    args = parser.parse_args()
    try:
        result = run_workflow_ticks(args.workspace,args.code_sha,args.max_scopes)
    except (ValueError,OSError,StateConflict,sqlite3.Error) as error:
        parser.exit(2,f'mock workflow error: {type(error).__name__}\n')
    print(json.dumps(result,ensure_ascii=False))
    return 2 if result['is_budget_limited'] or any(item['status']=='invalid' for item in result['processed']) else 0


if __name__=='__main__':
    raise SystemExit(main())
