from dataclasses import FrozenInstanceError
import copy
import json

import pytest

from application.frozen_healthcheck import (FrozenHealthcheckBundle, capture_frozen_healthcheck,
    validate_frozen_healthcheck_artifact)
from domain.snapshots import canonical_json
from local_tests.test_healthcheck_artifact import frozen_fixture, with_payload


def test_frozen_bundle_reconstructs_exact_documents_and_result(tmp_path):
    config, training, inputs, scope, code, artifact = frozen_fixture(tmp_path)
    bundle = capture_frozen_healthcheck(config, training, inputs, scope, code)
    assert bundle.documents() == (config, training, inputs)
    assert bundle.source_ids == (config.snapshot_id, training.snapshot_id, inputs.snapshot_id)
    validate_frozen_healthcheck_artifact(bundle, artifact)
    config.payload['rules'].clear()
    assert bundle.documents()[0] == config
    with pytest.raises(FrozenInstanceError):
        bundle.code_sha = 'changed'


@pytest.mark.parametrize('change', ['document', 'hash', 'extra', 'noncanonical', 'boolean_version'])
def test_changed_or_ambiguous_bundle_is_not_an_immutable_input(tmp_path, change):
    config, training, inputs, scope, code, _ = frozen_fixture(tmp_path)
    original = capture_frozen_healthcheck(config, training, inputs, scope, code)
    value = json.loads(original.serialized)
    if change == 'document': value['documents']['healthcheck_config']['document']['payload']['labels'] = []
    elif change == 'hash': value['documents']['training']['snapshot_id'] = 'b' * 64
    elif change == 'extra': value['unknown'] = 1
    elif change == 'boolean_version': value['schema_version'] = True
    serialized = json.dumps(value, indent=2) if change == 'noncanonical' else canonical_json(value)
    with pytest.raises(ValueError):
        FrozenHealthcheckBundle(serialized)


def test_scope_mismatch_is_rejected_before_a_child_can_be_started(tmp_path):
    config, training, inputs, scope, code, _ = frozen_fixture(tmp_path)
    other = with_payload(inputs, inputs.payload, scope_id='mock_other_union')
    with pytest.raises(ValueError, match='scope'):
        capture_frozen_healthcheck(config, training, other, scope, code)


@pytest.mark.parametrize('change', ['code', 'config', 'standards', 'layout', 'view', 'resource', 'extra'])
def test_output_cannot_change_sources_standards_route_or_typed_view(tmp_path, change):
    config, training, inputs, scope, code, artifact = frozen_fixture(tmp_path)
    bundle = capture_frozen_healthcheck(config, training, inputs, scope, code)
    value = copy.deepcopy(artifact)
    resource = next(iter(value['workbooks']))
    generated = value['workbooks'][resource]
    if change == 'code': generated['code_sha'] = 'mock-other-release'
    elif change == 'config': generated['config_id'] = 'a' * 64
    elif change == 'standards': generated['standards']['rules'][0]['metrics'][0]['threshold'] = 999
    elif change == 'layout': value['layout_revisions'][resource] = 'mock-other-layout'
    elif change == 'view': generated['view']['code_sha'] = 'mock-other-release'
    elif change == 'resource': value['workbooks']['mock_other_resource'] = value['workbooks'].pop(resource)
    else: generated['unknown'] = 1
    with pytest.raises(ValueError):
        validate_frozen_healthcheck_artifact(bundle, value)
