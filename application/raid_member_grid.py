"""Explicit mock human-input grid with stable identities, never nickname inference."""

from collections import Counter
from dataclasses import asdict
import json
import math
import re

from application.tasks import StateConflict
from domain.execution import check_execution
from domain.layout import encode_cell_value
from domain.raid import FixedInputs, RaidKnife, RaidMember
from domain.snapshots import MemberKey, capture_document

COLUMNS=('member_identity','sync','fixed_1','fixed_2','fixed_3',*(f'damage_{boss}_yi' for boss in range(1,6)))
GRID_FIELDS={'schema_version','union_inputs_id','column_ids','rows','history','formula_cells','is_complete'}


def capture_raid_member_grid(union_inputs,captured_at,grid):
    document=capture_document('union_inputs',union_inputs.envelope['scope_id'],captured_at,union_inputs.envelope['reference_version'],
        {'input_kind':'raid_member_grid','grid':grid})
    derive_raid_member_inputs(union_inputs,document)
    return document


def derive_raid_member_inputs(union_inputs,document):
    if (union_inputs.envelope['kind']!='union_inputs' or document.envelope['kind']!='union_inputs'
            or any(document.envelope[key]!=union_inputs.envelope[key] for key in ('scope_id','reference_version'))
            or set(document.payload)!={'input_kind','grid'} or document.payload['input_kind']!='raid_member_grid'
            or len(document.serialized.encode())>8*1024*1024):
        raise StateConflict('fixed scoped raid input grid required')
    grid=document.payload['grid']
    if (not isinstance(grid,dict) or set(grid)!=GRID_FIELDS or type(grid['schema_version']) is not int or grid['schema_version']!=1
            or grid['union_inputs_id']!=union_inputs.snapshot_id or grid['is_complete'] is not True
            or not isinstance(grid['column_ids'],list) or tuple(grid['column_ids'])!=COLUMNS
            or not isinstance(grid['rows'],list) or not 1<=len(grid['rows'])<=1000
            or grid['formula_cells']!=[] or not isinstance(grid['history'],list) or len(grid['history'])>900):
        raise ValueError('complete bounded literal grid with fixed column identities required')
    roster=union_inputs.payload['members']
    if not isinstance(roster,list) or not 1<=len(roster)<=300:
        raise ValueError('bounded current roster required')
    members=tuple(MemberKey(**row['member']) for row in roster)
    if len(set(members))!=len(members):raise ValueError('duplicate roster identity')
    names={MemberKey(**row['member']):row['display_name'] for row in roster}
    indexed={}
    for row in grid['rows']:
        check_execution()
        if not isinstance(row,list) or len(row)!=len(COLUMNS):raise ValueError('rectangular fixed raid input row required')
        for value in row:
            encode_cell_value(value)
            if isinstance(value,str) and len(value)>1024:raise ValueError('bounded input cell required')
        if all(value is None or value=='' for value in row):continue
        if not isinstance(row[0],str):raise ValueError('canonical composite member key required')
        identity=json.loads(row[0])
        if not isinstance(identity,list) or len(identity)!=2:raise ValueError('canonical composite member key required')
        member=MemberKey(*identity)
        if member.key!=row[0] or member not in members or member in indexed:
            raise ValueError('duplicate, foreign, or noncanonical raid member key')
        sync=_sync(row[1])
        RaidMember(member,names[member],sync)
        slots=tuple('' if value is None else value for value in row[2:5])
        if any(type(value) is float and abs(value)>2**53 for value in row[5:]):
            raise ValueError('numeric override exceeds exact binary64 input range; use decimal text')
        overrides=tuple((int(value) if value.is_integer() else str(value)) if type(value) is float else value for value in row[5:])
        fixed=FixedInputs(slots,overrides)
        indexed[member]={'member':asdict(member),'sync':sync,'fixed_inputs':asdict(fixed)}
    if set(indexed)!=set(members):raise ValueError('raid grid must cover the whole pinned roster')
    history=[]
    for data in grid['history']:
        check_execution()
        if not isinstance(data,dict):raise ValueError('typed history record required')
        knife=RaidKnife(**{**data,'member':MemberKey(**data['member'])})
        if knife.member not in indexed or knife.kind!='history':raise ValueError('history identity or kind differs')
        history.append(knife)
    if (len({knife.knife_id for knife in history})!=len(history)
            or any(count>3 for count in Counter(knife.member for knife in history).values())):
        raise ValueError('history knife identities or per-member count differ')
    derived=capture_document('union_inputs',union_inputs.envelope['scope_id'],document.envelope['captured_at'],union_inputs.envelope['reference_version'],
        {'input_kind':'raid_member_inputs','raid_schema_version':1,'members':[indexed[member] for member in members],
            'history':[asdict(knife) for knife in history]})
    if len(derived.serialized.encode())>8*1024*1024:raise ValueError('raid member inputs exceed byte bound')
    return derived


def _sync(value):
    if isinstance(value,str):
        if not re.fullmatch('[0-9]{1,6}',value):raise ValueError('sync must be explicit integer digits')
        value=int(value)
    elif type(value) is float:
        if not math.isfinite(value) or not value.is_integer():raise ValueError('integer sync required')
        value=int(value)
    if type(value) is not int or not 0<value<=100000:raise ValueError('positive bounded sync required')
    return value
