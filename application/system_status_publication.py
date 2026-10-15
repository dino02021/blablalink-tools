"""Frozen, scoped system-status snapshots and protected workbook projections."""

from dataclasses import asdict, dataclass
import hashlib
import re

from application.compute_budget import ComputeExecutionCheck, ComputeLimit
from application.system_status_view import render_system_status
from application.tasks import StateConflict, TaskSpec, validate_identifier
from domain.execution import check_execution, execution_checks
from domain.snapshots import canonical_json, capture_document
from domain.view_grid import ViewCell, ViewTab

SCOPE='system_status'
REFERENCE='system_status_v1'


def capture_system_status_route(captured_at, code_sha, resource, layout_revision, watched_scopes, is_enabled=True):
    if isinstance(watched_scopes,str):raise ValueError('explicit watched scope sequence required')
    watched=tuple(watched_scopes)
    for value in (code_sha,resource,layout_revision,*watched):validate_identifier(value)
    if (type(is_enabled) is not bool or not 1<=len(watched)<=100 or len(set(watched))!=len(watched) or SCOPE in watched
            or any(len(value)>256 for value in (code_sha,resource,layout_revision,*watched))):
        raise ValueError('bounded dedicated status route and explicit watched scopes required')
    return capture_document('union_inputs',SCOPE,captured_at,REFERENCE,
        {'input_kind':'system_status_route','status_schema_version':1,'code_sha':code_sha,'resource':resource,
            'layout_revision':layout_revision,'watched_scopes':sorted(watched),'is_enabled':is_enabled})


def read_system_status_route(document):
    data=document.payload
    if (document.envelope['kind']!='union_inputs' or document.envelope['scope_id']!=SCOPE or document.envelope['reference_version']!=REFERENCE
            or set(data)!={'input_kind','status_schema_version','code_sha','resource','layout_revision','watched_scopes','is_enabled'}
            or data['input_kind']!='system_status_route' or type(data['status_schema_version']) is not int or data['status_schema_version']!=1):
        raise ValueError('scoped system status route required')
    if capture_system_status_route(document.envelope['captured_at'],data['code_sha'],data['resource'],data['layout_revision'],data['watched_scopes'],data['is_enabled'])!=document:
        raise ValueError('canonical status route differs')
    return data


def validate_status_snapshot(status, watched_scopes):
    if (not isinstance(status,dict) or status.get('is_mock') is not True or status.get('is_read_only') is not True
            or type(status.get('schema_version')) is not int or status.get('schema_version')!=1 or status.get('is_restore_fenced') is not False
            or status.get('status_hash')!=_hash({key:value for key,value in status.items() if key!='status_hash'})
            or [row['scope_id'] for row in status['scopes']]!=list(watched_scopes)):
        raise StateConflict('frozen status snapshot or watched scope binding differs')


def status_publication_task(route, occurrence):
    data=read_system_status_route(route)
    payload=occurrence.payload
    if (occurrence.envelope['kind']!='union_inputs' or occurrence.envelope['scope_id']!=SCOPE or occurrence.envelope['reference_version']!=REFERENCE
            or set(payload)!={'input_kind','route_id','observation_id','status_hash','revision','code_sha'}
            or payload['input_kind']!='system_status_occurrence' or type(payload['revision']) is not int or payload['revision']<=0
            or payload['route_id']!=route.snapshot_id or payload['code_sha']!=data['code_sha']):
        raise StateConflict('status occurrence binding differs')
    return TaskSpec('publish_system_status',SCOPE,data['code_sha'],{'status_route':route.snapshot_id,'status_occurrence':occurrence.snapshot_id},family='system_status')


@dataclass(frozen=True)
class SystemStatusWorkbookView:
    route_id:str
    occurrence_id:str
    status_hash:str
    code_sha:str
    captured_at:str
    tabs:tuple[ViewTab,...]
    schema_version:int=1

    def __post_init__(self):
        tabs=tuple(self.tabs)
        if (type(self.schema_version) is not int or self.schema_version!=1
                or any(not isinstance(value,str) or re.fullmatch('[a-f0-9]{64}',value) is None for value in (self.route_id,self.occurrence_id,self.status_hash))
                or len(tabs)!=3 or any(not isinstance(tab,ViewTab) for tab in tabs) or len({tab.tab_key for tab in tabs})!=3
                or len({tab.title.casefold() for tab in tabs})!=3 or sum(len(tab.rows)*len(tab.column_ids) for tab in tabs)>500000):
            raise ValueError('bounded status workbook binding required')
        validate_identifier(self.code_sha)
        capture_document('union_inputs',SCOPE,self.captured_at,REFERENCE,{})
        object.__setattr__(self,'tabs',tabs)

    @property
    def view_id(self):return _hash(asdict(self))


def restore_system_status_workbook(data):
    tabs=tuple(ViewTab(**{**tab,'rows':tuple(tuple(ViewCell(**cell) for cell in row) for row in tab['rows'])}) for tab in data['tabs'])
    return SystemStatusWorkbookView(**{**data,'tabs':tabs})


def render_system_status_workbook(route,occurrence,observation):
    config=read_system_status_route(route)
    status_publication_task(route,occurrence)
    data=observation.payload
    if (observation.envelope['kind']!='union_inputs' or observation.envelope['scope_id']!=SCOPE or observation.envelope['reference_version']!=REFERENCE
            or set(data)!={'input_kind','route_id','code_sha','status'} or data['input_kind']!='system_status_observation'
            or data['route_id']!=route.snapshot_id or data['code_sha']!=config['code_sha']
            or occurrence.payload['observation_id']!=observation.snapshot_id):
        raise StateConflict('sealed status observation differs')
    status=data['status'];validate_status_snapshot(status,config['watched_scopes'])
    if status['status_hash']!=occurrence.payload['status_hash']:raise StateConflict('status occurrence content differs')
    tabs=[]
    for index in range(2):
        check_execution()
        subset={**status,'scopes':status['scopes'][index*50:(index+1)*50]}
        subset['status_hash']=_hash({key:value for key,value in subset.items() if key!='status_hash'})
        table=render_system_status(subset)
        tabs.append(ViewTab(f'system_status_{index+1}',f'聯盟狀態 {index+1}',table.column_ids,table.row_ids,table.rows,
            frozen_rows=1,frozen_columns=2))
    values=(('狀態內容觀測時間',occurrence.envelope['captured_at']),('觀察聯盟數',len(config['watched_scopes'])),
        ('快照狀態版本',status['status_hash']),('程式版本',config['code_sha']),
        ('資料說明','固定時間的協調狀態快照；成功紀錄不代表已回讀目前遠端工作簿'))
    rows=((ViewCell('項目','header'),ViewCell('內容','header')),*(tuple(ViewCell(value) for value in pair) for pair in values))
    tabs.append(ViewTab('system_status_meta','狀態快照資訊',('key','value'),('header',*(str(index) for index in range(len(values)))),rows,frozen_rows=1))
    return SystemStatusWorkbookView(route.snapshot_id,occurrence.snapshot_id,status['status_hash'],config['code_sha'],occurrence.envelope['captured_at'],tuple(tabs))


def run_system_status_job(store,lease,budget):
    try:
        with execution_checks(ComputeExecutionCheck(store,lease,budget)):
            check_execution()
            stale=store.supersede_if_stale(lease)
            if stale is not None:return stale
            job=store.get_job(lease.job_id)
            route=store.get_document(job.spec.version_map['status_route'])
            occurrence=store.get_document(job.spec.version_map['status_occurrence'])
            if status_publication_task(route,occurrence)!=job.spec:raise StateConflict('status task differs from sealed occurrence')
            observation=store.get_document(occurrence.payload['observation_id'])
            view=render_system_status_workbook(route,occurrence,observation)
            config=route.payload
            generated={'schema_version':1,'publication_kind':'system_status','config_id':route.snapshot_id,
                'snapshot_id':occurrence.snapshot_id,'calculation_id':view.status_hash,'code_sha':job.spec.code_sha,
                'status_workbook_view':asdict(view),'view_id':view.view_id}
            staged=capture_document('union_inputs',config['resource'],'1970-01-01T00:00:00Z',job.spec.code_sha,
                {'staging_kind':'workbook_generated','generated':generated})
            if len(staged.serialized.encode())>16*1024*1024:raise ComputeLimit('status_workbook_capacity')
            check_execution()
            return store.complete_compute(lease,{'workbooks':{config['resource']:generated},'layout_revisions':{config['resource']:config['layout_revision']}})
    except ComputeLimit as error:
        return store.defer(lease,'capacity_blocked',error.code)


def _hash(value):return hashlib.sha256(canonical_json(value).encode()).hexdigest()
