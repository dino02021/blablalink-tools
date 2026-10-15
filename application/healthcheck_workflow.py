"""Fixed local mock-only healthcheck follow-up policy, separate from runtime state."""

import re

from application.tasks import validate_identifier
from domain.snapshots import capture_document


def capture_healthcheck_workflow(scope_id, captured_at, reference_version, code_sha, reference_id, base_raw_id,
                                mock_script_id, config_scope_id='common', max_supplementary_rounds=3, is_enabled=True):
    validate_identifier(code_sha)
    if (len(code_sha)>128 or config_scope_id not in ('common',scope_id) or type(is_enabled) is not bool
            or type(max_supplementary_rounds) is not int or not 1<=max_supplementary_rounds<=3
            or any(not isinstance(value,str) or re.fullmatch('[a-f0-9]{64}',value) is None for value in (reference_id,base_raw_id,mock_script_id))):
        raise ValueError('bounded fixed mock workflow policy required')
    return capture_document('union_inputs',scope_id,captured_at,reference_version,
        {'input_kind':'mock_healthcheck_workflow','workflow_schema_version':1,'is_mock':True,'is_enabled':is_enabled,
            'code_sha':code_sha,'reference_id':reference_id,'base_raw_id':base_raw_id,'mock_script_id':mock_script_id,
            'config_scope_id':config_scope_id,'max_supplementary_rounds':max_supplementary_rounds})


def read_healthcheck_workflow(document):
    data = document.payload
    if (document.envelope['kind']!='union_inputs' or set(data)!={'input_kind','workflow_schema_version','is_mock','is_enabled',
            'code_sha','reference_id','base_raw_id','mock_script_id','config_scope_id','max_supplementary_rounds'}
            or data['input_kind']!='mock_healthcheck_workflow' or type(data['workflow_schema_version']) is not int
            or data['workflow_schema_version']!=1 or data['is_mock'] is not True):
        raise ValueError('supported mock workflow policy required')
    rebuilt = capture_healthcheck_workflow(document.envelope['scope_id'],document.envelope['captured_at'],document.envelope['reference_version'],
        data['code_sha'],data['reference_id'],data['base_raw_id'],data['mock_script_id'],data['config_scope_id'],
        data['max_supplementary_rounds'],data['is_enabled'])
    if rebuilt!=document:
        raise ValueError('workflow policy canonical content differs')
    return data
