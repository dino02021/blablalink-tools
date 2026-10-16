from dataclasses import FrozenInstanceError
import hashlib
import json
from pathlib import Path
import sys

import pytest

from application.frozen_healthcheck import capture_frozen_healthcheck
from domain.snapshots import MemberKey, canonical_json
from executors.local.frozen_healthcheck import run_frozen_healthcheck
from local_tests.test_healthcheck_artifact import frozen_fixture, with_payload

native = pytest.mark.skipif(sys.platform != 'win32', reason='requires verified Windows native child supervision')


@native
def test_native_frozen_result_matches_root_and_returned_payload_is_detached(tmp_path):
    config, training, inputs, scope, code, expected = frozen_fixture(tmp_path)
    bundle = capture_frozen_healthcheck(config, training, inputs, scope, code)
    result = run_frozen_healthcheck(bundle)
    assert result.state == 'computed' and result.receipt['outcome'] == 'completed'
    assert result.receipt['is_child_stopped'] is True and result.receipt['memory_limit_bytes'] == 512 * 1024 * 1024
    assert result.bundle_id == bundle.bundle_id and result.code_sha == code
    assert result.result_id == hashlib.sha256(canonical_json(expected).encode()).hexdigest()
    assert result.artifact == expected
    result.artifact['workbooks'].clear()
    assert result.artifact == expected
    with pytest.raises(FrozenInstanceError):
        result.state = 'failed'


@native
@pytest.mark.parametrize('reason', ['training_incomplete', 'training_membership_epoch_unproven'])
def test_native_pending_data_never_returns_a_result_or_zero_scores(tmp_path, reason):
    config, training, inputs, scope, code, _ = frozen_fixture(tmp_path)
    if reason == 'training_incomplete':
        training = with_payload(training, {**training.payload, 'records': []})
    else:
        epochs = {MemberKey(**row['member']).key: 'a' * 64 for row in inputs.payload['members']}
        inputs = with_payload(inputs, {**inputs.payload, 'member_epochs': epochs})
    result = run_frozen_healthcheck(capture_frozen_healthcheck(config, training, inputs, scope, code))
    assert result.state == 'waiting_for_data' and result.error_code == reason
    assert result.artifact is None and result.result_id is None
    assert result.receipt['outcome'] == 'completed' and result.receipt['is_child_stopped'] is True


@native
def test_large_native_result_uses_owned_sidecar_and_small_stdout_without_coordinator_files(tmp_path, monkeypatch):
    from executors.local import frozen_healthcheck
    config, training, inputs, scope, code, _ = frozen_fixture(tmp_path)
    config = with_payload(config, {**config.payload, 'mock_padding': 'x' * 100000})
    bundle = capture_frozen_healthcheck(config, training, inputs, scope, code)
    original = frozen_healthcheck.run_python_module
    observed = []
    def invoke(module, arguments, **kwargs):
        assert module == 'executors.local.frozen_healthcheck_child'
        assert kwargs['memory_limit_bytes'] == 512 * 1024 * 1024 and kwargs['output_limit_bytes'] == 65536
        result = original(module, arguments, **kwargs)
        directory = Path(arguments[1])
        assert {path.name for path in directory.iterdir()} == {'input.json', 'result.json', 'mock-frozen-healthcheck.json'}
        observed.append(len(result['stdout'].encode('utf-8')))
        return result
    monkeypatch.setattr(frozen_healthcheck, 'run_python_module', invoke)
    result = run_frozen_healthcheck(bundle)
    assert result.state == 'computed' and len(result.serialized_result.encode()) > 65536
    assert observed[0] < 1024
    assert next(iter(result.artifact['workbooks'].values()))['standards'] == config.payload


@native
def test_native_deadline_has_stopped_child_and_no_promotable_result(tmp_path):
    config, training, inputs, scope, code, _ = frozen_fixture(tmp_path)
    result = run_frozen_healthcheck(capture_frozen_healthcheck(config, training, inputs, scope, code), seconds=.05)
    assert result.state == 'execution_failed' and result.error_code == 'deadline_terminated'
    assert result.receipt['is_child_stopped'] is True and result.artifact is None and result.result_id is None


@native
@pytest.mark.parametrize('target', ['input', 'result'])
def test_native_tampered_input_or_result_is_never_accepted(tmp_path, monkeypatch, target):
    from executors.local import frozen_healthcheck
    config, training, inputs, scope, code, _ = frozen_fixture(tmp_path)
    bundle = capture_frozen_healthcheck(config, training, inputs, scope, code)
    original = frozen_healthcheck.run_python_module
    def invoke(module, arguments, **kwargs):
        directory = Path(arguments[1])
        if target == 'input':
            path = directory / 'input.json'
            path.write_bytes(path.read_bytes() + b' ')
        result = original(module, arguments, **kwargs)
        if target == 'result':
            path = directory / 'result.json'
            raw = path.read_bytes()
            path.write_bytes(raw[:-1] + b' ')
        return result
    monkeypatch.setattr(frozen_healthcheck, 'run_python_module', invoke)
    if target == 'input':
        result = run_frozen_healthcheck(bundle)
        assert result.state == 'execution_failed' and result.artifact is None
    else:
        with pytest.raises(ValueError, match='integrity'):
            run_frozen_healthcheck(bundle)


@pytest.mark.parametrize('change', ['version', 'bundle', 'code', 'provider', 'size', 'extra'])
def test_forged_header_cannot_promote_even_a_valid_sidecar(tmp_path, monkeypatch, change):
    from executors.local import frozen_healthcheck
    config, training, inputs, scope, code, artifact = frozen_fixture(tmp_path)
    bundle = capture_frozen_healthcheck(config, training, inputs, scope, code)
    raw = canonical_json(artifact).encode()
    header = {'schema_version': 1, 'is_mock': True, 'network_providers': [], 'bundle_id': bundle.bundle_id,
              'code_sha': code, 'state': 'computed', 'error_code': None,
              'result_id': hashlib.sha256(raw).hexdigest(), 'result_size': len(raw)}
    if change == 'version': header['schema_version'] = True
    elif change == 'bundle': header['bundle_id'] = 'b' * 64
    elif change == 'code': header['code_sha'] = 'mock-other-code'
    elif change == 'provider': header['network_providers'] = ['forbidden-mock-provider']
    elif change == 'size': header['result_size'] = True
    else: header['unknown'] = 1
    def invoke(module, arguments, **kwargs):
        (Path(arguments[1]) / 'result.json').write_bytes(raw)
        return {'outcome': 'completed', 'is_child_stopped': True, 'stdout': canonical_json(header)}
    monkeypatch.setattr(frozen_healthcheck, 'run_python_module', invoke)
    with pytest.raises(ValueError):
        run_frozen_healthcheck(bundle)


@pytest.mark.parametrize('seconds', [True, 0, 251, float('nan')])
def test_invalid_native_budget_never_starts_a_child(tmp_path, monkeypatch, seconds):
    from executors.local import frozen_healthcheck
    config, training, inputs, scope, code, _ = frozen_fixture(tmp_path)
    bundle = capture_frozen_healthcheck(config, training, inputs, scope, code)
    monkeypatch.setattr(frozen_healthcheck, 'run_python_module', lambda *args, **kwargs: pytest.fail('invalid child started'))
    with pytest.raises(ValueError):
        run_frozen_healthcheck(bundle, seconds=seconds)
