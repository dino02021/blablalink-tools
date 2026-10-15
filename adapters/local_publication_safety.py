"""Trusted local abort only after full generated-area baseline proof; no sheet writes."""

import hashlib
import json

from adapters.local_state import LocalStateStore, _transaction
from application.publication_safety import read_publication_abort, read_publication_state
from application.tasks import StateConflict, validate_identifier
from domain.snapshots import canonical_json


class LocalPublicationSafety(LocalStateStore):
    def commit_publication_abort(self, request_id, plan, confirmed_plan_id, is_all_writers_stopped):
        validate_identifier(request_id)
        data = read_publication_abort(plan)
        if confirmed_plan_id!=plan.snapshot_id or is_all_writers_stopped is not True:
            raise StateConflict('fixed abort confirmation and writer-stop proof required')
        body = {'publication_id':data['publication_id'],'plan_id':plan.snapshot_id,'actor_id':data['actor_id'],
            'code_sha':data['code_sha'],'outcome':'aborted_'+data['abort_mode']}
        receipt_id = _hash(body)
        with _transaction(self._connection):
            request = self._connection.execute('SELECT receipt_id FROM publication_abort_requests WHERE request_id=?',(request_id,)).fetchone()
            if request and request[0]!=receipt_id:
                raise StateConflict('abort request already binds another intent')
            prior = self._connection.execute('SELECT receipt_id FROM publication_abort_receipts WHERE publication_id=?',(data['publication_id'],)).fetchone()
            if prior:
                if prior[0]!=receipt_id:
                    raise StateConflict('publication already terminated by another abort intent')
            else:
                meta = self.publication_safety_metadata(data['publication_id'])
                if (meta['effective_state'] not in ('publishing','publish_uncertain') or meta['job_state'] not in ('publishing','publish_uncertain')
                        or not meta['has_safety_phase'] or plan.envelope['scope_id']!=meta['scope_id']
                        or any(data[key]!=meta[key] for key in ('generation','job_id','result_id','commit_id','code_sha'))
                        or tuple(data['resources'])!=meta['resources']):
                    raise StateConflict('publication abort generation or source changed')
                self._require_publication_locks(meta['publication_id'],meta['resources'])
                if data['abort_mode']=='not_started' and meta['is_send_started']!=0:
                    raise StateConflict('publication send already started')
                for resource in meta['resources'] if data['abort_mode']=='unchanged' else ():
                    base = self.publication_base(meta['publication_id'],resource)
                    if base is None or base.snapshot_id!=data['bases'][resource]:
                        raise StateConflict('original abort baseline is missing or changed')
                    observed = self.get_document(data['observations'][resource])
                    before,after = read_publication_state(base),read_publication_state(observed)
                    if (canonical_json(before)!=canonical_json(after) or before['resource']!=resource
                            or any(before[key]!=meta[key] for key in ('publication_id','commit_id','result_id','code_sha'))):
                        raise StateConflict('every generated resource must match its original baseline')
                self._put_object(plan.serialized)
                self._connection.execute('INSERT INTO publication_abort_receipts VALUES(?,?,?,?,?)',
                    (receipt_id,meta['publication_id'],plan.snapshot_id,canonical_json(body),self._now()))
                # Keep the legacy storage state as history; the effective-state view is terminal.
                self._connection.execute('UPDATE publications SET generation=generation+1,updated_at=? WHERE publication_id=?',(self._now(),meta['publication_id']))
                self._transition(meta['job_id'],'failed','publication_aborted_no_write',is_clear_lease=True)
                self._connection.execute('DELETE FROM resource_locks WHERE publication_id=?',(meta['publication_id'],))
            self._connection.execute('INSERT OR IGNORE INTO publication_abort_requests VALUES(?,?)',(request_id,receipt_id))
            return self.publication_abort_receipt(receipt_id)

    def publication_abort_receipt(self, receipt_id):
        row = self._connection.execute('SELECT * FROM publication_abort_receipts WHERE receipt_id=?',(receipt_id,)).fetchone()
        if row is None:raise StateConflict('unknown publication abort receipt')
        body = json.loads(row['signature'])
        plan = self.get_document(row['plan_id'])
        data = read_publication_abort(plan)
        if (_hash(body)!=receipt_id or body['plan_id']!=plan.snapshot_id or body['publication_id']!=row['publication_id']
                or data['publication_id']!=row['publication_id'] or body['actor_id']!=data['actor_id'] or body['code_sha']!=data['code_sha']
                or body['outcome']!='aborted_'+data['abort_mode']):
            raise StateConflict('publication abort receipt identity differs')
        return {**body,'receipt_id':receipt_id,'created_at':row['created_at']}


def _hash(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()
