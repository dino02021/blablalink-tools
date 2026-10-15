"""Durable scope-specific follow-up ticks; no provider calls or implicit publication."""

from dataclasses import asdict
import hashlib
import json

from adapters.local_raid_plans import LocalRaidPlans
from adapters.local_state import _transaction
from adapters.mock_collection_script import MockScriptCollection, capture_mock_collection_script
from application.healthcheck_workflow import read_healthcheck_workflow
from application.reference_catalog import read_reference
from application.supplementary_collection import merge_supplementary_raw, prepare_supplementary_inputs, supplementary_task_spec
from application.tasks import ResourceBusy, StateConflict, TaskSpec, validate_identifier
from application.training_reuse import plan_training_reuse
from domain.execution import check_execution, execution_checks
from domain.normalization import normalize_training
from domain.snapshots import MemberKey, Override, canonical_json, capture_document


class LocalHealthcheckWorkflows(LocalRaidPlans):
    def workflow_policy(self, scope_id):
        validate_identifier(scope_id)
        row = self._connection.execute('SELECT * FROM healthcheck_workflow_heads WHERE scope_id=?',(scope_id,)).fetchone()
        if row is None:
            return None
        document = self.get_document(row['policy_id'])
        if document.envelope['scope_id'] != scope_id:
            raise StateConflict('workflow policy scope differs')
        return {**dict(row),'policy':read_healthcheck_workflow(document)}

    def configure_healthcheck_workflow(self, request_id, document, confirmed_policy_id, expected_revision, actor_id):
        for value in (request_id,actor_id): validate_identifier(value)
        data = read_healthcheck_workflow(document)
        if document.snapshot_id!=confirmed_policy_id or type(expected_revision) is not int or expected_revision<0:
            raise StateConflict('workflow configuration confirmation differs')
        body = {'scope_id':document.envelope['scope_id'],'policy_id':document.snapshot_id,'actor_id':actor_id,
            'base_revision':expected_revision,'revision':expected_revision+1}
        receipt_id = _hash(body)
        with _transaction(self._connection):
            request = self._connection.execute('SELECT receipt_id FROM healthcheck_workflow_policy_requests WHERE request_id=?',(request_id,)).fetchone()
            if request and request[0]!=receipt_id:
                raise StateConflict('workflow configuration request already binds another intent')
            prior = self._connection.execute('SELECT signature FROM healthcheck_workflow_policy_receipts WHERE receipt_id=?',(receipt_id,)).fetchone()
            if prior is None:
                head = self.workflow_policy(body['scope_id'])
                if (head['revision'] if head else 0)!=expected_revision:
                    raise StateConflict('workflow policy revision changed')
                if head is None and self._connection.execute('SELECT COUNT(*) FROM healthcheck_workflow_heads').fetchone()[0]>=100:
                    raise StateConflict('workflow scope capacity reached')
                self._validate_sources(document)
                self._require_no_publisher()
                self._put_object(document.serialized)
                self._connection.execute('INSERT INTO healthcheck_workflow_heads VALUES(?,?,?) ON CONFLICT(scope_id) DO UPDATE SET policy_id=excluded.policy_id,revision=excluded.revision',
                    (body['scope_id'],document.snapshot_id,body['revision']))
                self._save_workflow_state(body['scope_id'],document.snapshot_id,
                    {'raw_snapshot_id':data['base_raw_id'],'cycle_key':None,'supplementary_rounds':0,'pending_collection_id':None,
                        'last_collection_id':None,'last_healthcheck_id':None,'last_plan_key':None,'status':'ready_to_reconcile','error_code':None})
                self._connection.execute('INSERT INTO healthcheck_workflow_policy_receipts VALUES(?,?,?)',(receipt_id,canonical_json(body),self._now()))
            elif _hash(json.loads(prior[0]))!=receipt_id:
                raise StateConflict('workflow policy receipt differs')
            self._connection.execute('INSERT OR IGNORE INTO healthcheck_workflow_policy_requests VALUES(?,?)',(request_id,receipt_id))
            return {**body,'receipt_id':receipt_id}

    def workflow_state(self, scope_id):
        head = self.workflow_policy(scope_id)
        if head is None:
            raise StateConflict('unknown workflow scope')
        row = self._connection.execute('SELECT * FROM healthcheck_workflow_state WHERE scope_id=?',(scope_id,)).fetchone()
        if row is None or row['policy_id']!=head['policy_id'] or row['state_hash']!=_hash({'scope_id':scope_id,'policy_id':row['policy_id'],'state':json.loads(row['state_json'])}):
            raise StateConflict('workflow state differs from policy or checksum')
        return {'scope_id':scope_id,'policy_id':row['policy_id'],'revision':row['revision'],'state':json.loads(row['state_json'])}

    def reconcile_healthcheck_workflow(self, scope_id, code_sha, budget):
        with execution_checks(budget.check):
            check_execution()
            with _transaction(self._connection):
                head = self.workflow_policy(scope_id)
                if head is None or head['policy']['code_sha']!=code_sha:
                    raise StateConflict('workflow release affinity differs')
                state = self.workflow_state(scope_id)['state']
                proposed = self._reconcile_workflow(head,state)
                check_execution()
                self._save_workflow_state(scope_id,head['policy_id'],proposed)
                return self.workflow_state(scope_id)

    def _validate_sources(self, document):
        data = document.payload
        reference = self.get_document(data['reference_id'])
        catalog, _ = read_reference(reference)
        raw = self.get_document(data['base_raw_id'])
        script = self.get_document(data['mock_script_id'])
        if (raw.envelope['kind']!='raw' or raw.envelope['scope_id']!=document.envelope['scope_id']
                or raw.envelope['reference_version']!=reference.envelope['reference_version']
                or reference.envelope['reference_version']!=document.envelope['reference_version']
                or raw.payload.get('reference_role_ids')!=list(catalog.reference.role_ids) or len(raw.serialized.encode())>16*1024*1024):
            raise StateConflict('workflow baseline or reference differs')
        members = list({MemberKey(**item['member']) for item in script.payload['sources']})
        inputs = capture_document('union_inputs',document.envelope['scope_id'],document.envelope['captured_at'],document.envelope['reference_version'],
            {'members':[asdict(member) for member in members]})
        MockScriptCollection(script,inputs)

    def _reconcile_workflow(self, head, state):
        scope, policy = head['scope_id'],head['policy']
        state = {**state,'error_code':None}
        if not policy['is_enabled']:
            return {**state,'status':'disabled'}
        input_head = self.union_input_version(scope)
        if input_head is None:
            return {**state,'status':'waiting_for_inputs'}
        if input_head['roster_state']=='inactive':
            return {**state,'status':'inactive','pending_collection_id':None,'last_plan_key':None}
        inputs = self.get_document(input_head['input_id'])
        config_row = self._connection.execute("SELECT config_id,revision FROM config_heads WHERE config_type='healthcheck' AND scope_id=?",(policy['config_scope_id'],)).fetchone()
        if config_row is None:
            return {**state,'status':'waiting_for_config'}
        config = self.get_document(config_row['config_id'])
        reference = self.get_document(policy['reference_id'])
        if len({document.envelope['reference_version'] for document in (inputs,config,reference)})!=1:
            return {**state,'status':'waiting_for_reference','error_code':'workflow_reference_transition_required'}
        cycle = _hash({'policy_id':head['policy_id'],'policy_revision':head['revision'],'input_id':input_head['input_id'],
            'input_revision':input_head['revision'],'config_id':config_row['config_id'],'config_revision':config_row['revision']})
        if state['pending_collection_id']:
            pending = self.get_job(state['pending_collection_id'])
            if pending.state=='succeeded':
                state['raw_snapshot_id'] = self._accepted_collection_raw(pending,state,policy)
                state.update(pending_collection_id=None,last_collection_id=pending.job_id,last_plan_key=None)
            elif pending.spec.version_map.get('union_inputs')!=input_head['input_id']:
                state.update(pending_collection_id=None,last_collection_id=pending.job_id,last_plan_key=None)
            else:
                return {**state,'status':'collecting' if pending.state in ('queued','retry_wait','running','dispatched') else pending.state,
                    'error_code':pending.error_code}
        if state['cycle_key']!=cycle:
            state.update(cycle_key=cycle,supplementary_rounds=0,last_plan_key=None,last_healthcheck_id=None)
        plan_key = _hash({'cycle_key':cycle,'raw_snapshot_id':state['raw_snapshot_id']})
        if state['last_plan_key']==plan_key and state['last_healthcheck_id']:
            job = self.get_job(state['last_healthcheck_id'])
            if not self.is_job_current(job.job_id):
                return {**state,'status':'waiting_for_owner','error_code':'workflow_desired_conflict'}
            return self._health_status(state,job)
        raw = self.get_document(state['raw_snapshot_id'])
        catalog,prefixes = read_reference(reference)
        rows = inputs.payload['members']
        roster = tuple(MemberKey(**row['member']) for row in rows)
        names = {MemberKey(**row['member']).key:row['display_name'] for row in rows}
        overrides = tuple(Override(**{**row,'member':MemberKey(**row['member'])}) for row in inputs.payload.get('overrides',[]))
        plan = plan_training_reuse(config,raw,catalog.reference,prefixes,roster,names,policy['code_sha'],overrides,inputs.payload.get('member_epochs'))
        if plan.blockers:
            return {**state,'status':'auth_required' if 'auth_required' in plan.blockers else 'blocked','error_code':'workflow_'+plan.blockers[0]}
        if plan.is_ready:
            training = normalize_training(raw,catalog.reference.role_ids,prefixes).training
            self._put_object(training.serialized)
            spec = TaskSpec('recompute_healthcheck',scope,policy['code_sha'],{'healthcheck_config':config.snapshot_id,
                'training':training.snapshot_id,'union_inputs':inputs.snapshot_id})
            try:
                job = self._enqueue('workflow-health:'+cycle+':'+spec.fingerprint,spec,True)
            except ResourceBusy:
                return {**state,'status':'waiting_for_publisher','error_code':'workflow_publisher_busy'}
            state.update(last_healthcheck_id=job.job_id,last_plan_key=plan_key)
            return self._health_status(state,job)
        if state['supplementary_rounds']>=policy['max_supplementary_rounds']:
            return {**state,'status':'capacity_blocked','error_code':'supplementary_round_limit'}
        prepared = prepare_supplementary_inputs(config,raw,reference,inputs,policy['code_sha'],self.get_document(head['policy_id']).envelope['captured_at'])
        script = self.get_document(policy['mock_script_id'])
        needed = {MemberKey(**member) for member in prepared.payload['members']}
        selected = [item for item in script.payload['sources'] if MemberKey(**item['member']) in needed]
        if {MemberKey(**item['member']) for item in selected}!=needed:
            return {**state,'status':'waiting_for_mock_data','error_code':'mock_member_fixture_missing'}
        projected = capture_mock_collection_script(scope,script.envelope['captured_at'],script.envelope['reference_version'],selected)
        MockScriptCollection(projected,prepared)
        self._put_object(prepared.serialized)
        self._put_object(projected.serialized)
        intent = _hash({'cycle_key':cycle,'plan_id':plan.plan_id,'round':state['supplementary_rounds']+1})
        spec = supplementary_task_spec(prepared,intent,projected.snapshot_id)
        job = self._enqueue('workflow-collection:'+intent,spec,False)
        return {**state,'pending_collection_id':job.job_id,'supplementary_rounds':state['supplementary_rounds']+1,'status':'collecting'}

    def _accepted_collection_raw(self, job, state, policy):
        result = self.get_result(job.result_id)
        inputs = self.get_document(job.spec.version_map['collection_inputs'])
        if (job.spec.command!='refresh_roster_and_training' or job.spec.code_sha!=policy['code_sha']
                or inputs.payload.get('collection_kind')!='supplementary_training_inputs'
                or result.get('base_raw_id')!=state['raw_snapshot_id'] or inputs.payload['base_raw_id']!=state['raw_snapshot_id']
                or result.get('collection_inputs_id')!=inputs.snapshot_id):
            raise StateConflict('workflow collection continuation differs')
        observation = self._connection.execute('SELECT 1 FROM collection_observations WHERE job_id=? AND generation=? AND raw_snapshot_id=? AND training_snapshot_id=? AND checkpoint_id=?',
            (job.job_id,job.generation,result['fresh_raw_id'],result['fresh_training_id'],result['collection_checkpoint_id'])).fetchone()
        raw = self.get_document(result['raw_snapshot_id'])
        reference = self.get_document(policy['reference_id'])
        catalog,prefixes = read_reference(reference)
        normalized = normalize_training(raw,catalog.reference.role_ids,prefixes)
        fresh = self.get_document(result['fresh_raw_id'])
        rebuilt = merge_supplementary_raw(self.get_document(state['raw_snapshot_id']),fresh,
            self.get_document(job.spec.version_map['union_inputs']),tuple(MemberKey(**member) for member in inputs.payload['members']))
        fresh_normalized = normalize_training(fresh,catalog.reference.role_ids,prefixes)
        lineage = raw.payload.get('supplementary_lineage',{})
        if (observation is None or rebuilt!=raw or fresh_normalized.training.snapshot_id!=result['fresh_training_id']
                or any(source['status']!='complete' for member in fresh.payload['collection_proof'] for source in member['sources'].values())
                or raw.envelope['scope_id']!=job.spec.scope_id or raw.envelope['reference_version']!=reference.envelope['reference_version']
                or lineage.get('base_raw_id')!=state['raw_snapshot_id'] or lineage.get('fresh_raw_id')!=result['fresh_raw_id']
                or lineage.get('union_inputs_id')!=job.spec.version_map['union_inputs']
                or normalized.training.snapshot_id!=result['training_snapshot_id']):
            raise StateConflict('workflow collection proof or normalization differs')
        return raw.snapshot_id

    def _health_status(self, state, job):
        if job.state=='succeeded':
            return {**state,'status':'health_published' if self.has_successful_publication(job.job_id) else 'blocked',
                'error_code':None if self.has_successful_publication(job.job_id) else 'health_publication_unproven'}
        return {**state,'status':'health_computed' if job.state=='computed' else 'health_pending' if job.state in ('queued','retry_wait','running','dispatched') else job.state,'error_code':job.error_code}

    def _save_workflow_state(self, scope_id, policy_id, state):
        digest = _hash({'scope_id':scope_id,'policy_id':policy_id,'state':state})
        row = self._connection.execute('SELECT state_hash FROM healthcheck_workflow_state WHERE scope_id=?',(scope_id,)).fetchone()
        if row is not None and row[0]==digest:
            return
        self._connection.execute('INSERT INTO healthcheck_workflow_state VALUES(?,?,?,?,1,?) ON CONFLICT(scope_id) DO UPDATE SET policy_id=excluded.policy_id,state_json=excluded.state_json,state_hash=excluded.state_hash,revision=revision+1,updated_at=excluded.updated_at',
            (scope_id,policy_id,canonical_json(state),digest,self._now()))


def _hash(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()
