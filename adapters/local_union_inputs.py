"""Explicit local roster adoption with version-fenced heads; no provider I/O."""

import hashlib
import json

from adapters.local_state import LocalStateStore, _transaction
from application.roster_lifecycle import RosterMember, capture_roster_observation, prepare_roster_change
from application.tasks import StateConflict, validate_identifier
from domain.snapshots import MemberKey, canonical_json


class LocalUnionInputs(LocalStateStore):
    def union_input_head(self, scope_id):
        validate_identifier(scope_id)
        row = self._connection.execute('SELECT * FROM union_input_heads WHERE scope_id=?', (scope_id,)).fetchone()
        if row is None:
            return None
        document = self.get_document(row['input_id'])
        state = _validate_inputs(document)
        if document.envelope['scope_id'] != scope_id or state != row['roster_state']:
            raise StateConflict('union input head differs from immutable document')
        return dict(row)

    def initialize_union_inputs(self, request_id, document, confirmed_input_id, actor_id, code_sha):
        state = _validate_inputs(document)
        for value in (request_id, actor_id, code_sha):
            validate_identifier(value)
        if document.snapshot_id != confirmed_input_id:
            raise StateConflict('initial union inputs confirmation differs')
        body = {'kind': 'initialize_union_inputs', 'scope_id': document.envelope['scope_id'], 'input_id': document.snapshot_id,
            'actor_id': actor_id, 'code_sha': code_sha, 'revision': 1, 'roster_state': state}
        with _transaction(self._connection):
            prior = self._replay_adoption(request_id, body)
            if prior is not None:
                return prior
            if self.union_input_head(body['scope_id']) is not None:
                raise StateConflict('union inputs are already initialized')
            self._require_no_publisher()
            self._put_object(document.serialized)
            self._connection.execute('INSERT INTO union_input_heads VALUES(?,?,1,?)', (body['scope_id'], document.snapshot_id, state))
            return self._save_adoption(request_id, body)

    def create_roster_candidate(self, plan, observation):
        with _transaction(self._connection):
            head = self.union_input_head(observation.envelope['scope_id'])
            if head is None or head['input_id'] != plan.base_inputs_id:
                raise StateConflict('roster candidate requires current union inputs')
            base = self.get_document(head['input_id'])
            rebuilt = prepare_roster_change(base, observation, plan.code_sha, plan.order_policy)
            if rebuilt != plan or plan.blockers or plan.target_inputs is None:
                raise StateConflict('roster candidate dependencies differ or are blocked')
            _validate_inputs(plan.target_inputs)
            candidate_id = _hash({'plan_id': plan.plan_id, 'base_revision': head['revision']})
            self._put_object(observation.serialized)
            self._put_object(plan.target_inputs.serialized)
            self._connection.execute('INSERT OR IGNORE INTO roster_candidates VALUES(?,?,?,?,?,?,?,?,?,?)',
                (candidate_id, head['scope_id'], head['input_id'], head['revision'], observation.snapshot_id,
                    plan.target_inputs.snapshot_id, plan.plan_id, plan.order_policy, plan.code_sha, self._now()))
            self.roster_candidate(candidate_id)
            return candidate_id

    def roster_candidate(self, candidate_id):
        row = self._connection.execute('SELECT * FROM roster_candidates WHERE candidate_id=?', (candidate_id,)).fetchone()
        if row is None:
            raise StateConflict('unknown roster candidate')
        plan = prepare_roster_change(self.get_document(row['base_input_id']), self.get_document(row['observation_id']), row['code_sha'], row['order_policy'])
        if (plan.blockers or plan.target_inputs is None or plan.plan_id != row['plan_id']
                or plan.target_inputs.snapshot_id != row['target_input_id']
                or plan.target_inputs.envelope['scope_id'] != row['scope_id']
                or candidate_id != _hash({'plan_id': plan.plan_id, 'base_revision': row['base_revision']})):
            raise StateConflict('roster candidate immutable dependencies differ')
        return dict(row)

    def adopt_roster_candidate(self, request_id, candidate_id, confirmed_candidate_id, actor_id, code_sha):
        for value in (request_id, candidate_id, confirmed_candidate_id, actor_id, code_sha):
            validate_identifier(value)
        with _transaction(self._connection):
            candidate = self.roster_candidate(candidate_id)
            if candidate_id != confirmed_candidate_id or candidate['code_sha'] != code_sha:
                raise StateConflict('roster adoption confirmation differs')
            target = self.get_document(candidate['target_input_id'])
            state = _validate_inputs(target)
            revision = candidate['base_revision'] + (candidate['target_input_id'] != candidate['base_input_id'])
            body = {'kind': 'adopt_roster_candidate', 'candidate_id': candidate_id, 'scope_id': candidate['scope_id'],
                'input_id': target.snapshot_id, 'base_input_id': candidate['base_input_id'], 'base_revision': candidate['base_revision'],
                'actor_id': actor_id, 'code_sha': code_sha, 'revision': revision, 'roster_state': state}
            prior = self._replay_adoption(request_id, body)
            if prior is not None:
                return prior
            head = self.union_input_head(candidate['scope_id'])
            if head is None or (head['input_id'], head['revision']) != (candidate['base_input_id'], candidate['base_revision']):
                raise StateConflict('roster adoption head changed')
            self._require_no_publisher()
            self._connection.execute('UPDATE union_input_heads SET input_id=?,revision=?,roster_state=? WHERE scope_id=?',
                (target.snapshot_id, revision, state, candidate['scope_id']))
            return self._save_adoption(request_id, body)

    def union_input_adoption(self, adoption_id):
        row = self._connection.execute('SELECT * FROM union_input_adoptions WHERE adoption_id=?', (adoption_id,)).fetchone()
        if row is None:
            raise StateConflict('unknown union input adoption')
        body = json.loads(row['signature'])
        if _hash(body) != adoption_id:
            raise StateConflict('union input adoption receipt differs')
        document = self.get_document(body['input_id'])
        if document.envelope['scope_id'] != body['scope_id'] or _validate_inputs(document) != body['roster_state']:
            raise StateConflict('union input adoption source differs')
        return {**body, 'adoption_id': adoption_id, 'created_at': row['created_at']}

    def _replay_adoption(self, request_id, body):
        adoption_id = _hash(body)
        request = self._connection.execute('SELECT adoption_id FROM union_input_requests WHERE request_id=?', (request_id,)).fetchone()
        if request is not None and request[0] != adoption_id:
            raise StateConflict('union input request already binds another intent')
        prior = self._connection.execute('SELECT 1 FROM union_input_adoptions WHERE adoption_id=?', (adoption_id,)).fetchone()
        if prior:
            self._connection.execute('INSERT OR IGNORE INTO union_input_requests VALUES(?,?)', (request_id, adoption_id))
            return self.union_input_adoption(adoption_id)
        return None

    def _save_adoption(self, request_id, body):
        adoption_id = _hash(body)
        self._connection.execute('INSERT INTO union_input_adoptions VALUES(?,?,?)', (adoption_id, canonical_json(body), self._now()))
        self._connection.execute('INSERT INTO union_input_requests VALUES(?,?)', (request_id, adoption_id))
        return self.union_input_adoption(adoption_id)


def _validate_inputs(document):
    envelope, payload = document.envelope, document.payload
    if envelope['kind'] != 'union_inputs' or payload.get('input_kind') == 'roster_observation':
        raise ValueError('union inputs document required')
    route = payload.get('healthcheck_route')
    if not isinstance(route, dict) or set(route) != {'resource', 'layout_revision'}:
        raise ValueError('fixed healthcheck workbook route required')
    for value in route.values():
        validate_identifier(value)
    observation = capture_roster_observation(envelope['scope_id'], envelope['captured_at'], envelope['reference_version'],
        tuple(RosterMember(MemberKey(**row['member']), row['display_name']) for row in payload['members']), 'complete', 'local_input_validation')
    plan = prepare_roster_change(document, observation, 'local_input_validation')
    if not plan.is_noop:
        raise StateConflict('union input validation unexpectedly changes roster')
    return 'active' if payload['members'] else 'inactive'


def _hash(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()
