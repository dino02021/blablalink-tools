"""Known legacy A/B member and A:W plan mock ranges; explicit row identity mapping."""

from dataclasses import asdict,dataclass
import hashlib
import json

from application.raid_member_grid import COLUMNS,capture_raid_member_grid
from application.tasks import StateConflict,validate_identifier
from domain.execution import check_execution
from domain.layout import encode_cell_value
from domain.snapshots import MemberKey,canonical_json

SOURCE_KEYS=('legacy_raid_member_rows','legacy_raid_plan_rows','legacy_raid_history','legacy_raid_is_complete','legacy_raid_formula_cells')


@dataclass(frozen=True)
class LegacyRaidGridPlan:
    resource:str
    layout_revision:str
    union_inputs_id:str
    captured_at:str
    code_sha:str
    source_hash:str
    member_row_ids:tuple[tuple[int,str],...]
    plan_row_ids:tuple[tuple[int,str],...]
    member_links:tuple[tuple[int,str,str],...]
    plan_links:tuple[tuple[int,str,str],...]
    target_grid_json:str
    grid_snapshot_id:str

    @property
    def plan_id(self):return _hash(asdict(self))


def legacy_raid_source(book):
    inputs=book['inputs']
    if any(key not in inputs for key in SOURCE_KEYS):raise ValueError('explicit complete legacy raid source required')
    source={key:inputs[key] for key in SOURCE_KEYS}
    if len(canonical_json(source).encode())>8*1024*1024:raise ValueError('legacy raid source exceeds byte bound')
    if source['legacy_raid_is_complete'] is not True or source['legacy_raid_formula_cells']!=[]:
        raise ValueError('complete literal legacy input ranges required')
    return source


def prepare_legacy_raid_grid(resource,book,union_inputs,member_row_ids,plan_row_ids,captured_at,code_sha):
    for value in (resource,book['layout_revision'],code_sha):validate_identifier(value)
    if 'raid_member_grid' in book['inputs']:raise StateConflict('existing stable raid grid cannot be overwritten by migration')
    source=legacy_raid_source(book)
    members=tuple(MemberKey(**row['member']).key for row in union_inputs.payload['members'])
    if not 1<=len(members)<=300 or len(set(members))!=len(members):raise ValueError('bounded unique roster required')
    member_mapping,member_links=_map_rows(source['legacy_raid_member_rows'],2,2,member_row_ids,members)
    plan_mapping,plan_links=_map_rows(source['legacy_raid_plan_rows'],23,3,plan_row_ids,members)
    rows=[]
    for member in members:
        check_execution()
        member_row=member_mapping[member]
        plan_row=plan_mapping[member]
        # Known GS source: A2:B members; A3 names and P3:W three slots/five overrides.
        rows.append([member,member_row[1],*plan_row[15:23]])
    grid={'schema_version':1,'union_inputs_id':union_inputs.snapshot_id,'column_ids':list(COLUMNS),'rows':rows,
        'history':source['legacy_raid_history'],'formula_cells':[],'is_complete':True}
    document=capture_raid_member_grid(union_inputs,captured_at,grid)
    return LegacyRaidGridPlan(resource,book['layout_revision'],union_inputs.snapshot_id,captured_at,code_sha,_hash(source),
        tuple(sorted(member_row_ids.items())),tuple(sorted(plan_row_ids.items())),tuple(member_links),tuple(plan_links),
        canonical_json(grid),document.snapshot_id)


def confirm_legacy_raid_grid(plan,book,union_inputs,confirmed_plan_id,code_sha):
    if confirmed_plan_id!=plan.plan_id or union_inputs.snapshot_id!=plan.union_inputs_id or code_sha!=plan.code_sha:
        raise StateConflict('fixed legacy raid migration confirmation differs')
    rebuilt=prepare_legacy_raid_grid(plan.resource,book,union_inputs,dict(plan.member_row_ids),dict(plan.plan_row_ids),plan.captured_at,code_sha)
    if rebuilt!=plan:raise StateConflict('legacy raid source, mapping, or layout changed')
    return json.loads(plan.target_grid_json)


def _map_rows(rows,width,start_row,mapping,members):
    if (not isinstance(rows,list) or not 1<=len(rows)<=1000 or not isinstance(mapping,dict)
            or len(mapping)!=len(members) or any(type(row) is not int or row<start_row for row in mapping)):
        raise ValueError('explicit physical row to stable member mapping required')
    mapped,links,used={},[],set()
    for index,row in enumerate(rows,start_row):
        check_execution()
        if not isinstance(row,list) or len(row)!=width:raise ValueError('known rectangular legacy range required')
        for value in row:
            encode_cell_value(value)
            if isinstance(value,str) and len(value)>1024:raise ValueError('bounded legacy cell required')
        if all(value is None or value=='' for value in row):continue
        if not isinstance(row[0],str) or not row[0].strip():raise ValueError('legacy source row label is missing')
        member=mapping.get(index)
        if member not in members or member in mapped:raise ValueError('all legacy rows need explicit unique current member mapping')
        mapped[member]=row
        links.append((index,row[0],member))
        used.add(index)
    if set(mapped)!=set(members) or used!=set(mapping):raise ValueError('complete exact legacy row mapping required')
    return mapped,links


def _hash(value):return hashlib.sha256(canonical_json(value).encode()).hexdigest()
