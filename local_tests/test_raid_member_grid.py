"""Mock editable grid snapshots never infer member identity from row order/name."""

import json

import pytest

from application.raid_member_grid import COLUMNS, capture_raid_member_grid, derive_raid_member_inputs
from application.tasks import StateConflict
from domain.snapshots import MemberKey, capture_document
from executors.local.worker import run_worker
from local_tests.test_raid_sources import registry, prepared, compiled
from local_tests.test_raid_inputs import TIME


def grid_for(inputs):
    return {'schema_version':1,'union_inputs_id':inputs.snapshot_id,'column_ids':list(COLUMNS),'is_complete':True,
        'formula_cells':[],'history':[],
        'rows':[[MemberKey(**row['member']).key,100,'負一','','',0,'',None,'20.5',30.5] for row in reversed(inputs.payload['members'])]}


def test_capture_preserves_original_values_and_reorders_only_by_stable_roster(compiled):
    _,_,_,inputs,_,_,_=compiled
    grid=grid_for(inputs)
    grid['rows'][0][2]='=literal_fixed_token'
    raw=capture_raid_member_grid(inputs,TIME,grid)
    derived=derive_raid_member_inputs(inputs,raw)
    assert [row['member'] for row in derived.payload['members']]==[row['member'] for row in inputs.payload['members']]
    assert raw.payload['grid']==grid
    assert derived.payload['members'][-1]['fixed_inputs']['slots'][0]=='=literal_fixed_token'
    assert derived.payload['members'][0]['fixed_inputs']['damage_overrides_yi']==[0,'',None,'20.5','30.5']
    assert len({row['display_name'] for row in inputs.payload['members']})==1
    grid['rows'][0][2]='later edit'
    assert raw.payload['grid']['rows'][0][2]=='=literal_fixed_token'


@pytest.mark.parametrize('change',['duplicate','missing','foreign','noncanonical','nickname','columns','partial','formula',
    'sync_bool','sync_fraction','override_bool','override_large_float','shape','extra','wrong_union','schema_bool'])
def test_ambiguous_or_incomplete_grid_fails_closed(compiled,change):
    _,_,_,inputs,_,_,_=compiled
    grid=grid_for(inputs)
    if change=='duplicate':grid['rows'].append(grid['rows'][0])
    elif change=='missing':grid['rows'].pop()
    elif change=='foreign':grid['rows'][0][0]=MemberKey('area_demo','foreign').key
    elif change=='noncanonical':grid['rows'][0][0]=json.dumps(json.loads(grid['rows'][0][0]),indent=1)
    elif change=='nickname':grid['rows'][0][0]='same_mock_name'
    elif change=='columns':grid['column_ids'][2]='fixed_alias'
    elif change=='partial':grid['is_complete']=False
    elif change=='formula':grid['formula_cells']=[[0,5]]
    elif change=='sync_bool':grid['rows'][0][1]=True
    elif change=='sync_fraction':grid['rows'][0][1]=100.5
    elif change=='override_bool':grid['rows'][0][5]=True
    elif change=='override_large_float':grid['rows'][0][5]=1e30
    elif change=='shape':grid['rows'][0].pop()
    elif change=='extra':grid['unbound_field']='value'
    elif change=='wrong_union':grid['union_inputs_id']='0'*64
    elif change=='schema_bool':grid['schema_version']=True
    with pytest.raises((ValueError,StateConflict)):capture_raid_member_grid(inputs,TIME,grid)


def test_integral_sheet_sync_and_blank_trailing_row_are_explicit(compiled):
    _,_,_,inputs,_,_,_=compiled
    grid=grid_for(inputs)
    grid['rows'][0][1]=100.0
    grid['rows'][1][1]='101'
    grid['rows'].append([None]*len(COLUMNS))
    raw=capture_raid_member_grid(inputs,TIME,grid)
    derived=derive_raid_member_inputs(inputs,raw)
    assert sorted(row['sync'] for row in derived.payload['members'])==[100,101]


@pytest.mark.parametrize('change',['foreign','duplicate','too_many','wrong_kind'])
def test_history_is_separately_typed_and_bound_to_same_members(compiled,change):
    _,_,_,inputs,_,_,_=compiled
    grid=grid_for(inputs)
    member=inputs.payload['members'][0]['member']
    knife={'knife_id':'history','member':member,'boss':1,'damage_true':'100','kind':'history','stage':1,'is_locked':True}
    grid['history']=[knife]
    if change=='foreign':knife['member']={'area_id':'area_demo','member_id':'foreign'}
    elif change=='duplicate':grid['history'].append(dict(knife))
    elif change=='too_many':grid['history']=[{**knife,'knife_id':str(i)} for i in range(4)]
    elif change=='wrong_kind':knife['kind']='auto'
    with pytest.raises(ValueError):capture_raid_member_grid(inputs,TIME,grid)


def test_mock_sheet_capture_candidate_adoption_and_worker_keep_frozen_source(registry):
    directory,store,health,_,confirmation,_,route,_,sheets,_=registry
    inputs=store.get_document(health.spec.version_map['union_inputs'])
    settings=store.raid_source_candidate(registry[-1]).payload['settings_id']
    grid=grid_for(inputs)
    sheets.create('raid_human_input','input_layout',{'raid_member_grid':grid})
    raw=capture_raid_member_grid(inputs,TIME,sheets.read('raid_human_input')['inputs']['raid_member_grid'])
    grid['rows'][0][2]='正五'
    sheets.edit_inputs('raid_human_input',{'raid_member_grid':grid})
    candidate=store.create_raid_source_candidate_from_grid(health.job_id,settings,raw,route.snapshot_id,TIME,health.spec.code_sha)
    bundle=store.raid_source_candidate(candidate)
    assert bundle.payload['member_grid_id']==raw.snapshot_id
    assert store.get_document(bundle.payload['member_inputs_id']).payload['members'][-1]['fixed_inputs']['slots'][0]=='負一'
    assert store.raid_source_version('union_demo') is None
    adopted=store.adopt_raid_sources('grid-adopt',candidate,candidate,'actor',health.spec.code_sha,True)
    result=run_worker(directory,health.spec.code_sha,is_publish_enabled=True,max_workflow_checks=0)
    assert result['processed'][0]['job_id']==adopted['followup']['job_id']
    assert result['processed'][0]['state']=='succeeded'
    assert sheets.read('raid_human_input')['inputs']['raid_member_grid']['rows'][0][2]=='正五'
    assert sheets.read('raid_output')['generated']=={} and store.confirmed_head('union_demo')==confirmation


def test_grid_candidate_derivation_failure_rolls_back_objects_and_candidate(registry):
    _,store,health,_,_,_,route,_,_,_=registry
    inputs=store.get_document(health.spec.version_map['union_inputs'])
    raw=capture_raid_member_grid(inputs,TIME,grid_for(inputs))
    before=store._connection.execute('SELECT COUNT(*) FROM objects').fetchone()[0]
    with pytest.raises(KeyError):store.create_raid_source_candidate_from_grid(health.job_id,'missing-settings',raw,route.snapshot_id,TIME,health.spec.code_sha)
    assert store._connection.execute('SELECT COUNT(*) FROM objects').fetchone()[0]==before


def test_float_zero_remains_numeric_zero_and_source_context_is_fixed(compiled):
    _,_,_,inputs,_,_,_=compiled
    grid=grid_for(inputs)
    grid['rows'][0][5]=0.0
    raw=capture_raid_member_grid(inputs,TIME,grid)
    derived=derive_raid_member_inputs(inputs,raw)
    assert derived.payload['members'][-1]['fixed_inputs']['damage_overrides_yi'][0]==0
    assert type(derived.payload['members'][-1]['fixed_inputs']['damage_overrides_yi'][0]) is int
    other=capture_document('union_inputs','another',TIME,inputs.envelope['reference_version'],raw.payload)
    with pytest.raises(StateConflict):derive_raid_member_inputs(inputs,other)


def test_retention_keeps_raw_grid_provenance_through_candidate_reference(registry):
    from contextlib import closing
    from adapters.local_retention import LocalObjectRetention
    directory,store,health,_,_,_,route,_,_,candidate=registry
    inputs=store.get_document(health.spec.version_map['union_inputs'])
    settings=store.raid_source_candidate(candidate).payload['settings_id']
    raw=capture_raid_member_grid(inputs,TIME,grid_for(inputs))
    candidate=store.create_raid_source_candidate_from_grid(health.job_id,settings,raw,route.snapshot_id,TIME,health.spec.code_sha)
    bundle=store.raid_source_candidate(candidate)
    with closing(LocalObjectRetention(directory/'state.sqlite')) as retention:
        plan=retention.plan_retention(99999999999)
        assert not {candidate,raw.snapshot_id,bundle.payload['member_inputs_id']}&set(plan.object_ids)
