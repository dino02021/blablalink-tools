"""Mock generated-area baselines and explicit unchanged-publication abort plans."""

import re

from application.tasks import StateConflict, validate_identifier
from domain.snapshots import canonical_json, capture_document

STATE_FIELDS = {'is_missing','layout_revision','commit_id','payload_hash','generated_hash','grid_hash','layout_hash'}
PLAN_FIELDS = {'operation_kind','publication_id','generation','job_id','result_id','commit_id','resources','code_sha','actor_id',
    'bases','observations','is_all_writers_stopped','abort_mode'}


def capture_publication_state(resource, publication_id, commit_id, result_id, code_sha, owned_state):
    payload = {'observation_kind':'mock_publication_state','resource':resource,'publication_id':publication_id,
        'commit_id':commit_id,'result_id':result_id,'code_sha':code_sha,'owned_state':owned_state}
    document = capture_document('union_inputs',resource,'1970-01-01T00:00:00Z','mock_publication_state_v1',payload)
    read_publication_state(document)
    return document


def read_publication_state(document):
    data = document.payload
    if (document.envelope['kind']!='union_inputs' or document.envelope['reference_version']!='mock_publication_state_v1'
            or set(data)!={'observation_kind','resource','publication_id','commit_id','result_id','code_sha','owned_state'}
            or data['observation_kind']!='mock_publication_state' or data['resource']!=document.envelope['scope_id']):
        raise ValueError('explicit mock publication state required')
    for key in ('resource','publication_id','commit_id','code_sha'): validate_identifier(data[key])
    if not _is_hash(data['result_id']): raise ValueError('sealed publication result required')
    state = data['owned_state']
    if not isinstance(state,dict) or type(state.get('is_missing')) is not bool:
        raise ValueError('explicit generated-area existence required')
    if state['is_missing']:
        if set(state)!={'is_missing'}:raise ValueError('missing resource cannot carry generated state')
    else:
        if (set(state)!=STATE_FIELDS or not isinstance(state['layout_revision'],str) or not state['layout_revision']
                or state['commit_id'] is not None and not isinstance(state['commit_id'],str)
                or state['payload_hash'] is not None and not _is_hash(state['payload_hash'])
                or any(not _is_hash(state[key]) for key in ('generated_hash','grid_hash','layout_hash'))):
            raise ValueError('bounded generated-area hashes required')
    if len(document.serialized.encode())>16384:raise ValueError('publication state exceeds bound')
    return data


def prepare_publication_abort(store, sheets, publication_id, actor_id, is_all_writers_stopped):
    if is_all_writers_stopped is not True:
        raise StateConflict('all generated-area writers must be stopped before observation')
    validate_identifier(actor_id)
    meta = store.publication_safety_metadata(publication_id)
    if meta['effective_state'] not in ('publishing','publish_uncertain') or not meta['has_safety_phase'] or len(meta['resources'])>100:
        raise StateConflict('publication has no eligible original baseline')
    bases,observations = {},{}
    mode = 'not_started' if meta['is_send_started']==0 else 'unchanged'
    if mode=='unchanged':
        for resource in meta['resources']:
            base = store.publication_base(publication_id,resource)
            if base is None:raise StateConflict('publication baseline is missing')
            current = sheets.capture_publication_state(resource,publication_id,meta['commit_id'],meta['result_id'],meta['code_sha'])
            before,after = read_publication_state(base),read_publication_state(current)
            if canonical_json(before)!=canonical_json(after):
                raise StateConflict('generated area is not proven unchanged or restored')
            bases[resource]=base.snapshot_id
            observations[resource]=store.put_document(current)
    payload = {'operation_kind':'abort_unchanged_publication','publication_id':publication_id,'generation':meta['generation'],
        'job_id':meta['job_id'],'result_id':meta['result_id'],'commit_id':meta['commit_id'],'resources':list(meta['resources']),
        'code_sha':meta['code_sha'],'actor_id':actor_id,'bases':bases,'observations':observations,'is_all_writers_stopped':True,'abort_mode':mode}
    plan = capture_document('union_inputs',meta['scope_id'],'1970-01-01T00:00:00Z','publication_abort_v1',payload)
    read_publication_abort(plan)
    return plan


def read_publication_abort(plan):
    data = plan.payload
    if (plan.envelope['kind']!='union_inputs' or plan.envelope['reference_version']!='publication_abort_v1'
            or set(data)!=PLAN_FIELDS or data['operation_kind']!='abort_unchanged_publication'
            or type(data['generation']) is not int or data['generation']<=0 or data['is_all_writers_stopped'] is not True
            or data['abort_mode'] not in ('not_started','unchanged')
            or not isinstance(data['resources'],list) or not 1<=len(data['resources'])<=100
            or data['resources']!=sorted(set(data['resources'])) or len(plan.serialized.encode())>512*1024):
        raise ValueError('fixed bounded publication abort plan required')
    for key in ('publication_id','job_id','commit_id','code_sha','actor_id'):validate_identifier(data[key])
    if not _is_hash(data['result_id']):raise ValueError('abort result binding required')
    for resource in data['resources']:validate_identifier(resource)
    for field in ('bases','observations'):
        expected = set(data['resources']) if data['abort_mode']=='unchanged' else set()
        if (not isinstance(data[field],dict) or set(data[field])!=expected
                or any(not _is_hash(value) for value in data[field].values())):
            raise ValueError('all abort resources need fixed proof objects')
    return data


def _is_hash(value):
    return isinstance(value,str) and re.fullmatch('[a-f0-9]{64}',value) is not None
