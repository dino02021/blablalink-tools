"""Approved local shared-status route and content/occurrence-deduplicated snapshots."""

import hashlib
import json
from pathlib import Path

from adapters.local_state import LocalStateStore, _transaction
from adapters.local_system_status import read_system_status
from adapters.mock_workspace import require_mock_workspace
from application.system_status_publication import SCOPE,REFERENCE,read_system_status_route,status_publication_task,validate_status_snapshot
from application.tasks import StateConflict,validate_identifier
from domain.execution import check_execution
from domain.snapshots import canonical_json,capture_document


class LocalSystemStatusPublications(LocalStateStore):
    def system_status_route(self):
        row=self._connection.execute('SELECT * FROM system_status_route_heads WHERE scope_id=?',(SCOPE,)).fetchone()
        if row is None:return None
        document=self.get_document(row['route_id'])
        return {**dict(row),'route':read_system_status_route(document)}

    def adopt_system_status_route(self,request_id,document,confirmed_route_id,expected_revision,actor_id):
        for value in (request_id,actor_id):validate_identifier(value)
        read_system_status_route(document)
        if document.snapshot_id!=confirmed_route_id or type(expected_revision) is not int or expected_revision<0:
            raise StateConflict('shared status route confirmation differs')
        body={'route_id':document.snapshot_id,'base_revision':expected_revision,'revision':expected_revision+1,'actor_id':actor_id}
        receipt_id=_hash(body)
        with _transaction(self._connection):
            request=self._connection.execute('SELECT receipt_id FROM system_status_route_requests WHERE request_id=?',(request_id,)).fetchone()
            if request and request[0]!=receipt_id:raise StateConflict('status route request already binds another intent')
            prior=self._connection.execute('SELECT signature FROM system_status_route_receipts WHERE receipt_id=?',(receipt_id,)).fetchone()
            if prior is None:
                head=self._connection.execute('SELECT revision FROM system_status_route_heads WHERE scope_id=?',(SCOPE,)).fetchone()
                if (head[0] if head else 0)!=expected_revision:raise StateConflict('status route head changed')
                if self.union_input_version(SCOPE) is not None:raise StateConflict('status owner scope is reserved outside union membership')
                self._require_no_publisher()
                self._put_object(document.serialized)
                self._connection.execute('INSERT INTO system_status_route_heads VALUES(?,?,?) ON CONFLICT(scope_id) DO UPDATE SET route_id=excluded.route_id,revision=excluded.revision',
                    (SCOPE,document.snapshot_id,body['revision']))
                self._connection.execute('INSERT INTO system_status_route_receipts VALUES(?,?,?)',(receipt_id,canonical_json(body),self._now()))
            elif _hash(json.loads(prior[0]))!=receipt_id:raise StateConflict('status route receipt differs')
            self._connection.execute('INSERT OR IGNORE INTO system_status_route_requests VALUES(?,?)',(request_id,receipt_id))
            return {**body,'receipt_id':receipt_id}

    def observe_system_status(self,directory,code_sha,captured_at):
        directory=require_mock_workspace(directory,is_writer=True)
        database=Path(self._connection.execute('PRAGMA database_list').fetchone()[2]).resolve()
        if database!=directory/'state.sqlite':raise StateConflict('status snapshot database differs from coordinator')
        with _transaction(self._connection):
            head=self.system_status_route()
            if head is None or head['route']['code_sha']!=code_sha or not head['route']['is_enabled']:return None
            if self.union_input_version(SCOPE) is not None:raise StateConflict('status owner scope is reserved outside union membership')
            self._require_no_publisher()
            check_execution()
            status=read_system_status(directory,tuple(head['route']['watched_scopes']))
            validate_status_snapshot(status,head['route']['watched_scopes'])
            semantic=_hash({'route_id':head['route_id'],'status_hash':status['status_hash'],'code_sha':code_sha})
            prior=self._connection.execute('SELECT observation_id FROM system_status_observations WHERE semantic_id=?',(semantic,)).fetchone()
            if prior:
                observation=self.get_document(prior[0])
                if (observation.payload.get('route_id')!=head['route_id'] or observation.payload.get('code_sha')!=code_sha
                        or canonical_json(observation.payload.get('status'))!=canonical_json(status)):
                    raise StateConflict('deduplicated status observation differs')
                self._connection.execute('UPDATE system_status_observations SET last_checked_at=? WHERE observation_id=?',(self._now(),prior[0]))
            else:
                observation=capture_document('union_inputs',SCOPE,captured_at,REFERENCE,
                    {'input_kind':'system_status_observation','route_id':head['route_id'],'code_sha':code_sha,'status':status})
                if len(observation.serialized.encode())>8*1024*1024:raise StateConflict('status observation exceeds local bound')
                self._put_object(observation.serialized)
                self._connection.execute('INSERT INTO system_status_observations VALUES(?,?,?,?,?)',(observation.snapshot_id,semantic,status['status_hash'],head['route_id'],self._now()))
            active=self._connection.execute('SELECT * FROM system_status_observation_heads WHERE scope_id=?',(SCOPE,)).fetchone()
            occurrence=self.get_document(active['occurrence_id']) if active else None
            if occurrence is None or occurrence.payload.get('observation_id')!=observation.snapshot_id or occurrence.payload.get('route_id')!=head['route_id']:
                revision=(active['revision'] if active else 0)+1
                occurrence=capture_document('union_inputs',SCOPE,captured_at,REFERENCE,
                    {'input_kind':'system_status_occurrence','route_id':head['route_id'],'observation_id':observation.snapshot_id,
                        'status_hash':status['status_hash'],'revision':revision,'code_sha':code_sha})
                self._put_object(occurrence.serialized)
                self._connection.execute('INSERT INTO system_status_occurrences VALUES(?,?,?)',(occurrence.snapshot_id,observation.snapshot_id,revision))
                self._connection.execute('INSERT INTO system_status_observation_heads VALUES(?,?,?) ON CONFLICT(scope_id) DO UPDATE SET occurrence_id=excluded.occurrence_id,revision=excluded.revision',
                    (SCOPE,occurrence.snapshot_id,revision))
            spec=status_publication_task(self.get_document(head['route_id']),occurrence)
            return self._enqueue('shared-status:'+str(head['revision'])+':'+occurrence.snapshot_id,spec,True)


def _hash(value):return hashlib.sha256(canonical_json(value).encode()).hexdigest()
