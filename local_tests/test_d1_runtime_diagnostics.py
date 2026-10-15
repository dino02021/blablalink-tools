import json

import pytest

from application.tasks import StateConflict
import executors.local.d1_receipt_runtime as module


@pytest.mark.parametrize('result,detail', [
    ({'outcome': 'emulator_failed', 'error_name': 'InjectedSdkFailure', 'error_message': 'offline startup failed'}, 'InjectedSdkFailure'),
    ({'is_mock': True, 'operations': []}, 'operation_count'),
])
def test_failed_comparison_preserves_bounded_runtime_diagnostic_without_accepting_it(monkeypatch, result, detail):
    monkeypatch.setattr(module, 'run_d1_contract_runtime', lambda: {'miniflare_version': 'fixed', 'workerd_version': 'fixed'})
    monkeypatch.setattr(module, 'run_d1_emulator', lambda *args, **kwargs: {
        'outcome': 'completed', 'is_child_stopped': True, 'stdout': json.dumps(result)})
    with pytest.raises(StateConflict, match=detail):
        module.run_receipt_d1_comparison()
