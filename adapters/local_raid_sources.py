"""Atomic local adoption of raid settings, human inputs and publication route."""

from dataclasses import asdict, replace
import hashlib
import json

from adapters.local_raid_plans import LocalRaidPlans
from adapters.local_state import _transaction
from application.raid_inputs import compile_raid_problem, raid_task_spec
from application.raid_member_grid import derive_raid_member_inputs
from application.raid_publication import read_raid_publication_route
from application.tasks import StateConflict, validate_identifier
from domain.snapshots import canonical_json, capture_document
from domain.execution import check_execution


class LocalRaidSources(LocalRaidPlans):
    def create_raid_source_candidate(self, healthcheck_job_id, settings_id, member_inputs_id, route_id, captured_at, code_sha):
        with _transaction(self._connection):
            return self._create_raid_source_candidate(healthcheck_job_id, settings_id, member_inputs_id, route_id, captured_at, code_sha)

    def create_raid_source_candidate_from_grid(self, healthcheck_job_id, settings_id, grid_document, route_id, captured_at, code_sha):
        with _transaction(self._connection):
            health = self.get_job(healthcheck_job_id)
            inputs = self.get_document(health.spec.version_map['union_inputs'])
            derived = derive_raid_member_inputs(inputs,grid_document)
            self._put_object(grid_document.serialized)
            self._put_object(derived.serialized)
            return self._create_raid_source_candidate(healthcheck_job_id,settings_id,derived.snapshot_id,route_id,captured_at,code_sha,grid_document.snapshot_id)

    def raid_source_candidate(self, candidate_id):
        row = self._connection.execute('SELECT * FROM raid_source_candidates WHERE candidate_id=?', (candidate_id,)).fetchone()
        if row is None:
            raise StateConflict('unknown raid source candidate')
        bundle = self.get_document(candidate_id)
        data = bundle.payload
        fields = {'input_kind', 'raid_schema_version', 'base_bundle_id', 'base_revision', 'union_inputs', 'healthcheck_job',
            'healthcheck_result', 'settings_id', 'member_inputs_id', 'route_id', 'code_sha', 'problem_id'}
        if (bundle.envelope['scope_id'] != row['scope_id'] or bundle.envelope['kind'] != 'union_inputs'
                or set(data) not in (fields,fields|{'member_grid_id'}) or data['input_kind'] != 'raid_source_bundle' or type(data['raid_schema_version']) is not int
                or data['raid_schema_version'] != 1 or type(data['base_revision']) is not int or data['base_revision'] < 0
                or (data['base_bundle_id'] is None) != (data['base_revision'] == 0)):
            raise StateConflict('raid source candidate schema differs')
        if 'member_grid_id' in data:
            derived = derive_raid_member_inputs(self.get_document(data['union_inputs']),self.get_document(data['member_grid_id']))
            if derived.snapshot_id != data['member_inputs_id']:
                raise StateConflict('raid candidate member grid derivation differs')
        return bundle

    def adopt_raid_sources(self, request_id, candidate_id, confirmed_candidate_id, actor_id, code_sha, is_generate_draft=False):
        for value in (request_id, candidate_id, confirmed_candidate_id, actor_id, code_sha):
            validate_identifier(value)
        if candidate_id != confirmed_candidate_id:
            raise StateConflict('raid source confirmation differs')
        if type(is_generate_draft) is not bool:
            raise ValueError('explicit raid draft followup choice required')
        with _transaction(self._connection):
            bundle = self.raid_source_candidate(candidate_id)
            data, scope = bundle.payload, bundle.envelope['scope_id']
            if data['code_sha'] != code_sha:
                raise StateConflict('raid source candidate code differs')
            body = {'candidate_id': candidate_id, 'scope_id': scope, 'actor_id': actor_id, 'code_sha': code_sha,
                'revision': data['base_revision']+1}
            if is_generate_draft:
                body['is_generate_draft'] = True
            adoption_id = _hash(body)
            request = self._connection.execute('SELECT adoption_id FROM raid_source_requests WHERE request_id=?', (request_id,)).fetchone()
            if request and request[0] != adoption_id:
                raise StateConflict('raid source request already binds another intent')
            prior = self._connection.execute('SELECT 1 FROM raid_source_adoptions WHERE adoption_id=?', (adoption_id,)).fetchone()
            if prior is None:
                head = self.raid_source_version(scope)
                if ((head['bundle_id'], head['revision']) if head else (None, 0)) != (data['base_bundle_id'], data['base_revision']):
                    raise StateConflict('raid source adoption head changed')
                problem = compile_raid_problem(self, data['healthcheck_job'], data['settings_id'], data['member_inputs_id'], code_sha)
                health = self.get_job(data['healthcheck_job'])
                if (problem.problem_id != data['problem_id'] or health.result_id != data['healthcheck_result']
                        or health.spec.version_map['union_inputs'] != data['union_inputs']):
                    raise StateConflict('raid source adoption dependencies differ')
                read_raid_publication_route(self, scope, bundle.envelope['reference_version'], data['route_id'])
                self._require_no_publisher()
                self._connection.execute('INSERT INTO raid_source_heads VALUES(?,?,?) ON CONFLICT(scope_id) DO UPDATE SET bundle_id=excluded.bundle_id,revision=excluded.revision',
                    (scope, candidate_id, body['revision']))
                self._connection.execute('INSERT INTO raid_source_adoptions VALUES(?,?,?)', (adoption_id, canonical_json(body), self._now()))
                if is_generate_draft:
                    check_execution()
                    current_problem = self.input_head(scope)
                    self._observe_problem(scope, problem, current_problem['revision'] if current_problem else 0)
                    job = self._enqueue('raid-followup:'+adoption_id, _followup_spec(scope,problem,candidate_id), True)
                    self._connection.execute('INSERT INTO raid_source_followups VALUES(?,?,?)', (adoption_id,job.job_id,problem.problem_id))
            self._connection.execute('INSERT OR IGNORE INTO raid_source_requests VALUES(?,?)', (request_id, adoption_id))
            return self.raid_source_adoption(adoption_id)

    def raid_source_adoption(self, adoption_id):
        row = self._connection.execute('SELECT * FROM raid_source_adoptions WHERE adoption_id=?', (adoption_id,)).fetchone()
        if row is None:
            raise StateConflict('unknown raid source adoption')
        body = json.loads(row['signature'])
        if _hash(body) != adoption_id:
            raise StateConflict('raid source adoption receipt differs')
        bundle = self.raid_source_candidate(body['candidate_id'])
        if body['scope_id'] != bundle.envelope['scope_id'] or body['code_sha'] != bundle.payload['code_sha'] or body['revision'] != bundle.payload['base_revision']+1:
            raise StateConflict('raid source receipt dependencies differ')
        followup = self._connection.execute('SELECT job_id,problem_id FROM raid_source_followups WHERE adoption_id=?',(adoption_id,)).fetchone()
        extra = {}
        if body.get('is_generate_draft') is True:
            from application.raid_codec import restore_problem
            if followup is None or followup['problem_id']!=bundle.payload['problem_id']:
                raise StateConflict('raid adoption followup is missing or differs')
            problem = restore_problem(self.get_result(followup['problem_id']))
            job = self.get_job(followup['job_id'])
            if job.spec != _followup_spec(body['scope_id'],problem,bundle.snapshot_id):
                raise StateConflict('raid adoption followup task differs')
            extra['followup'] = dict(followup)
        elif followup is not None:
            raise StateConflict('unexpected raid source followup')
        return {**body, **extra, 'adoption_id': adoption_id, 'created_at': row['created_at']}

    def _create_raid_source_candidate(self, healthcheck_job_id, settings_id, member_inputs_id, route_id, captured_at, code_sha, member_grid_id=None):
        problem = compile_raid_problem(self, healthcheck_job_id, settings_id, member_inputs_id, code_sha)
        health = self.get_job(healthcheck_job_id)
        inputs = self.get_document(health.spec.version_map['union_inputs'])
        scope, reference = inputs.envelope['scope_id'], inputs.envelope['reference_version']
        read_raid_publication_route(self, scope, reference, route_id)
        head = self.raid_source_version(scope)
        bundle = capture_document('union_inputs', scope, captured_at, reference,
            {'input_kind': 'raid_source_bundle', 'raid_schema_version': 1,
                'base_bundle_id': head['bundle_id'] if head else None, 'base_revision': head['revision'] if head else 0,
                'union_inputs': inputs.snapshot_id, 'healthcheck_job': healthcheck_job_id, 'healthcheck_result': health.result_id,
                'settings_id': settings_id, 'member_inputs_id': member_inputs_id, 'route_id': route_id,
                'code_sha': code_sha, 'problem_id': problem.problem_id})
        if member_grid_id is not None:
            payload = {**bundle.payload,'member_grid_id':member_grid_id}
            bundle = capture_document('union_inputs',scope,captured_at,reference,payload)
        self._put_object(canonical_json(asdict(problem)))
        self._put_object(bundle.serialized)
        self._connection.execute('INSERT OR IGNORE INTO raid_source_candidates VALUES(?,?)', (bundle.snapshot_id, scope))
        return bundle.snapshot_id


def _hash(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def _followup_spec(scope, problem, bundle_id):
    spec = raid_task_spec(scope,problem)
    return replace(spec,versions={**spec.version_map,'raid_source_bundle':bundle_id})
