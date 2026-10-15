"""Known legacy mock ranges require explicit physical-row identities and fresh CAS."""

from dataclasses import replace

import pytest

from adapters.legacy_raid_grid import prepare_legacy_raid_grid
from adapters.mock_sheets import MockSheets
from application.raid_member_grid import capture_raid_member_grid,derive_raid_member_inputs
from application.tasks import StateConflict
from domain.snapshots import MemberKey
from local_tests.test_raid_inputs import TIME,compiled


@pytest.fixture
def legacy(compiled):
    directory,_,_,inputs,_,_,_=compiled
    members=[MemberKey(**row['member']).key for row in inputs.payload['members']]
    member_rows=[['same_old_name',120],['same_old_name',130]]
    plan_rows=[['same_old_name',*(['untouched_output']*14),'負一','','',0,'',None,'25.5',30],
        ['same_old_name',*(['different_output']*14),'正五','正五','',1,2,3,4,5]]
    source={'legacy_raid_member_rows':member_rows,'legacy_raid_plan_rows':plan_rows,'legacy_raid_history':[],
        'legacy_raid_is_complete':True,'legacy_raid_formula_cells':[],'manual_notes':{'keep':0}}
    sheets=MockSheets(directory/'workbooks')
    sheets.create('legacy_raid','legacy_layout',source)
    sheets.apply('legacy_raid','legacy_layout',{'old_result':'keep'},'original_commit')
    sheets.set_manual_tab('legacy_raid','notes','人工備註',[['keep']])
    member_map={2:members[0],3:members[1]}
    plan_map={3:members[1],4:members[0]}
    plan=prepare_legacy_raid_grid('legacy_raid',sheets.read('legacy_raid'),inputs,member_map,plan_map,TIME,'release')
    return sheets,inputs,plan,member_map,plan_map


def test_preview_is_pure_and_explicit_row_mappings_preserve_reordered_values(legacy):
    sheets,inputs,plan,_,_=legacy
    before=sheets.read('legacy_raid')
    assert 'raid_member_grid' not in before['inputs']
    assert [link[1] for link in plan.member_links]==['same_old_name','same_old_name']
    assert plan.member_links[0][2]!=plan.plan_links[0][2]
    result=sheets.apply_legacy_raid_grid(plan,inputs,plan.plan_id,'release')
    assert result==plan.grid_snapshot_id
    after=sheets.read('legacy_raid')
    assert after['generated']==before['generated'] and after['commit_id']==before['commit_id']
    assert after['manual_tabs']==before['manual_tabs'] and after['layout_revision']==before['layout_revision']
    assert all(after['inputs'][key]==value for key,value in before['inputs'].items())
    raw=capture_raid_member_grid(inputs,TIME,after['inputs']['raid_member_grid'])
    derived=derive_raid_member_inputs(inputs,raw)
    assert raw.snapshot_id==plan.grid_snapshot_id
    assert derived.payload['members'][0]['sync']==120
    assert derived.payload['members'][0]['fixed_inputs']['slots']==['正五','正五','']
    assert derived.payload['members'][1]['fixed_inputs']['damage_overrides_yi']==[0,'',None,'25.5',30]
    assert after['raid_input_migrations'][0]['source']['legacy_raid_plan_rows']==before['inputs']['legacy_raid_plan_rows']


def test_apply_replay_is_idempotent_and_preserves_later_unrelated_human_edits(legacy):
    sheets,inputs,plan,_,_=legacy
    latest=sheets.read('legacy_raid')['inputs']
    latest['manual_notes']['keep']='newer collaborator note'
    sheets.edit_inputs('legacy_raid',latest)
    sheets.apply_legacy_raid_grid(plan,inputs,plan.plan_id,'release')
    before=sheets.read('legacy_raid')
    assert sheets.apply_legacy_raid_grid(plan,inputs,plan.plan_id,'release')==plan.grid_snapshot_id
    assert sheets.read('legacy_raid')==before and before['inputs']['manual_notes']['keep']=='newer collaborator note'


@pytest.mark.parametrize('change',['member_value','fixed_value','output_value','history','layout','confirmation','code'])
def test_changed_source_or_intent_never_overwrites_collaborator_data(legacy,change):
    sheets,inputs,plan,_,_=legacy
    book=sheets.read('legacy_raid')
    confirmed,code=plan.plan_id,'release'
    if change=='member_value':book['inputs']['legacy_raid_member_rows'][0][1]=140
    elif change=='fixed_value':book['inputs']['legacy_raid_plan_rows'][0][15]='正三'
    elif change=='output_value':book['inputs']['legacy_raid_plan_rows'][0][1]='later output'
    elif change=='history':book['inputs']['legacy_raid_history'].append({'unresolved':'history'})
    elif change=='layout':book['layout_revision']='changed_layout'
    elif change=='confirmation':confirmed='0'*64
    elif change=='code':code='another_release'
    sheets._save('legacy_raid',book)
    before=sheets.read('legacy_raid')
    with pytest.raises((ValueError,StateConflict,TypeError,KeyError)):sheets.apply_legacy_raid_grid(plan,inputs,confirmed,code)
    assert sheets.read('legacy_raid')==before


@pytest.mark.parametrize('change',['missing_map','duplicate_member','foreign','extra_blank_map','missing_row','wrong_width','blank_name','partial','formula'])
def test_missing_or_ambiguous_identity_metadata_is_not_guessed(legacy,change):
    sheets,inputs,_,member_map,plan_map=legacy
    book=sheets.read('legacy_raid')
    if change=='missing_map':member_map={}
    elif change=='duplicate_member':member_map[3]=member_map[2]
    elif change=='foreign':member_map[2]=MemberKey('area','foreign').key
    elif change=='extra_blank_map':member_map[4]=member_map[2]
    elif change=='missing_row':book['inputs']['legacy_raid_plan_rows'].pop()
    elif change=='wrong_width':book['inputs']['legacy_raid_plan_rows'][0].append('unknown field')
    elif change=='blank_name':book['inputs']['legacy_raid_plan_rows'][0][0]=''
    elif change=='partial':book['inputs']['legacy_raid_is_complete']=False
    elif change=='formula':book['inputs']['legacy_raid_formula_cells']=[['plan',3,16]]
    with pytest.raises(ValueError):prepare_legacy_raid_grid('legacy_raid',book,inputs,member_map,plan_map,TIME,'release')


def test_existing_stable_input_and_changed_target_are_never_replaced(legacy):
    sheets,inputs,plan,member_map,plan_map=legacy
    sheets.apply_legacy_raid_grid(plan,inputs,plan.plan_id,'release')
    current=sheets.read('legacy_raid')
    with pytest.raises(StateConflict):prepare_legacy_raid_grid('legacy_raid',current,inputs,member_map,plan_map,TIME,'release')
    current['inputs']['raid_member_grid']['rows'][0][2]='負四'
    sheets.edit_inputs('legacy_raid',current['inputs'])
    before=sheets.read('legacy_raid')
    with pytest.raises(StateConflict):sheets.apply_legacy_raid_grid(plan,inputs,plan.plan_id,'release')
    assert sheets.read('legacy_raid')==before


def test_forged_plan_cannot_change_target_values(legacy):
    sheets,inputs,plan,_,_=legacy
    forged=replace(plan,target_grid_json=plan.target_grid_json.replace('120','999'))
    before=sheets.read('legacy_raid')
    with pytest.raises(StateConflict):sheets.apply_legacy_raid_grid(forged,inputs,forged.plan_id,'release')
    assert sheets.read('legacy_raid')==before


@pytest.mark.parametrize('change_source',[False,True])
def test_concurrent_human_edit_and_migration_share_one_read_modify_write_lock(legacy,change_source):
    from concurrent.futures import ThreadPoolExecutor,TimeoutError
    from threading import Event
    from adapters.mock_workbook_lock import workbook_file_lock
    sheets,inputs,plan,_,_=legacy
    started=Event()
    def apply():
        started.set()
        return sheets.apply_legacy_raid_grid(plan,inputs,plan.plan_id,'release')
    with ThreadPoolExecutor(max_workers=1) as pool:
        with workbook_file_lock(sheets._path('legacy_raid')):
            future=pool.submit(apply)
            assert started.wait(2)
            with pytest.raises(TimeoutError):future.result(timeout=.1)
            updated=sheets.read('legacy_raid')['inputs']
            if change_source:updated['legacy_raid_member_rows'][0][1]=150
            else:updated['manual_notes']['keep']='concurrent edit'
            sheets.edit_inputs('legacy_raid',updated)
        if change_source:
            with pytest.raises(StateConflict):future.result(timeout=5)
            assert 'raid_member_grid' not in sheets.read('legacy_raid')['inputs']
        else:
            assert future.result(timeout=5)==plan.grid_snapshot_id
            assert sheets.read('legacy_raid')['inputs']['manual_notes']['keep']=='concurrent edit'


def test_modified_archive_receipt_is_not_used_as_replay_authority(legacy):
    sheets,inputs,plan,_,_=legacy
    sheets.apply_legacy_raid_grid(plan,inputs,plan.plan_id,'release')
    book=sheets.read('legacy_raid')
    book['raid_input_migrations'][0]['source']['legacy_raid_plan_rows'][0][15]='changed archive'
    sheets._save('legacy_raid',book)
    with pytest.raises(StateConflict,match='integrity'):sheets.apply_legacy_raid_grid(plan,inputs,plan.plan_id,'release')


def test_receipt_capacity_blocks_without_dropping_old_manual_history(legacy):
    from dataclasses import asdict
    import json
    from domain.snapshots import canonical_json
    from adapters.legacy_raid_grid import legacy_raid_source
    sheets,inputs,plan,member_map,plan_map=legacy
    book=sheets.read('legacy_raid')
    source=legacy_raid_source(book)
    receipts=[]
    for index in range(20):
        prior=prepare_legacy_raid_grid('legacy_raid',book,inputs,member_map,plan_map,f'2026-10-15T00:01:{index:02}Z','release')
        receipts.append({'plan_id':prior.plan_id,'plan':json.loads(canonical_json(asdict(prior))),'source_hash':prior.source_hash,'source':source})
    book['raid_input_migrations']=receipts
    sheets._save('legacy_raid',book)
    before=sheets.read('legacy_raid')
    with pytest.raises(StateConflict,match='capacity'):sheets.apply_legacy_raid_grid(plan,inputs,plan.plan_id,'release')
    assert sheets.read('legacy_raid')==before
