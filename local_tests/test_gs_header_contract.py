"""Frozen GS header construction, with explicit limits on layout compatibility."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from adapters.legacy_standard import HEADERS
from application.healthcheck import calculate_healthcheck
from application.workbook_views import FIELD_LABELS, render_healthcheck_view
from domain.healthcheck import ELEMENTS, MetricRule, RoleRule
from domain.snapshots import MemberKey, RoleLabel, TrainingRecord, capture_config, capture_training
from domain.standard_layout import ABBREVIATIONS, GROUPS, METRICS

ROOT=Path(__file__).resolve().parent
TRAITS=((),('優越代碼傷害','攻擊力','最大裝彈數'),('蓄力速度','暴擊率','暴擊傷害'),
    ('蓄力傷害','命中率','防禦力'),('未知詞條','攻擊力'))


def run_reference(cases,directory):
    fixture=ROOT/'fixtures/gs_header_reference.js'
    provenance=json.loads((ROOT/'fixtures/gs_header_provenance.json').read_text(encoding='utf-8'))
    assert hashlib.sha256(fixture.read_text(encoding='utf-8').encode()).hexdigest()==provenance['fixture_sha256']
    node=shutil.which('node')
    assert node,'local frozen GS header contract requires Node'
    env={key:os.environ[key] for key in ('PATH','SYSTEMROOT','WINDIR','TEMP','TMP') if key in os.environ}
    result=subprocess.run([node,str(ROOT/'gs_header_runner.js')],input=json.dumps(cases),text=True,encoding='utf-8',
        capture_output=True,check=True,timeout=10,cwd=directory,env=env)
    return json.loads(result.stdout)


def render_python(traits,name='=literal_role'):
    member=MemberKey('area','member')
    functions=tuple(f'function_{index}' for index in range(len(traits)))
    rule=RoleRule('role',('電擊',),'output',tuple(MetricRule(field,None,1) for field in METRICS)+
        tuple(MetricRule(f'trait_{i+1}',None,1,function) for i,function in enumerate(functions)),1)
    config=capture_config('common','2026-10-16T00:00:00Z','reference',(rule,),
        (RoleLabel('role',name,trait_labels=tuple(zip(functions,traits))),))
    training=capture_training('union','2026-10-16T00:00:00Z','reference',
        (TrainingRecord(member,'role','owned',{field:0 for field in METRICS},{function:0 for function in functions}),))
    result=calculate_healthcheck(config,training,(member,),{member.key:'same_name'},'release')
    return render_healthcheck_view(config,training,(member,),{member.key:'same_name'},result)


@pytest.mark.parametrize('traits',TRAITS)
def test_standard_all_twelve_slots_groups_and_merges_match_frozen_gs(tmp_path,traits):
    reference=run_reference([{'kind':'standard','name':'=literal_role','traits':traits}],tmp_path)[0]
    view=render_python(traits)
    standard=view.tabs[0]
    assert reference['isolated']
    assert reference['rows']==[[cell.value for cell in standard.rows[index][1:13]] for index in (1,2)]
    expected_merges=sorted(list(merge) for merge in standard.merges if merge[0]!=0)
    assert sorted(reference['merges'])==expected_merges
    assert len(reference['widths'])==12
    assert reference['headers']==list(HEADERS)
    assert reference['elements']==list(ELEMENTS)
    assert reference['abbreviations']==ABBREVIATIONS
    assert reference['legacyHeaders']==[header for header in HEADERS if header!='評分追蹤']
    ids=[json.loads(column) for column in standard.column_ids[1:]]
    assert [identity[2] for identity in ids]==[*METRICS,'trait_1','trait_2','trait_3']


def test_training_group_header_difference_is_explicit_and_identity_order_matches(tmp_path):
    traits=TRAITS[1]
    reference=run_reference([{'kind':'training','name':'=literal_role','traits':traits}],tmp_path)[0]
    view=render_python(traits)
    training=next(tab for tab in view.tabs if tab.tab_key=='training_電擊')
    assert [cell.value for cell in training.rows[0][2:]]==reference['rows'][0]
    # Old GS merges group labels on row 2; Python exposes each metric/function.
    # Record this deliberate difference instead of claiming drop-in grid parity.
    assert [cell.value for cell in training.rows[1][2:]]!=reference['rows'][1]
    assert [cell.value for cell in training.rows[1][2:11]]==[FIELD_LABELS[field] for field in METRICS]
    assert [cell.value for cell in training.rows[1][11:]]==list(traits)
    assert len(training.merges)==1 and len(reference['merges'])==4
    assert [json.loads(column)[2] for column in training.column_ids[2:]]==[*METRICS,'trait_1','trait_2','trait_3']
    assert training.typed_rows()[0][2]['value']=={'stringValue':'=literal_role'}


def test_trait_display_normalization_difference_is_recorded(tmp_path):
    traits=(' 攻擊力 ',)
    reference=run_reference([{'kind':'standard','name':'mock_role','traits':traits}],tmp_path)[0]
    view=render_python(traits,'mock_role')
    assert reference['traitLabel']=='詞條 (攻)'
    assert view.tabs[0].rows[2][10].value=='詞條 ( 攻擊力 )'
    # Presentation differs for noncanonical display dictionaries; the function ID
    # remains stable. Production dictionary normalization is not silently assumed.
    assert json.loads(view.tabs[0].column_ids[10])[-1]=='function_0'
