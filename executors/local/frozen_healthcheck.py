"""Owned native calculation of immutable mock input; parent verifies sidecars."""

from dataclasses import dataclass, field
import hashlib
import json
import math
from pathlib import Path
import tempfile

from application.frozen_healthcheck import FrozenHealthcheckBundle, validate_frozen_healthcheck_artifact
from application.healthcheck_artifact import HealthcheckInputsPending
from domain.chunks import MAX_OBJECT_BYTES
from domain.snapshots import canonical_json
from executors.local.process_guard import run_python_module


@dataclass(frozen=True)
class FrozenHealthcheckExecution:
    bundle_id: str
    code_sha: str
    state: str
    error_code: str | None
    serialized_result: str | None = field(repr=False)
    serialized_receipt: str = field(repr=False)

    @property
    def artifact(self):
        return None if self.serialized_result is None else json.loads(self.serialized_result)

    @property
    def result_id(self):
        return None if self.serialized_result is None else hashlib.sha256(self.serialized_result.encode('utf-8')).hexdigest()

    @property
    def receipt(self):
        return json.loads(self.serialized_receipt)


def run_frozen_healthcheck(bundle, *, seconds=250):
    if not isinstance(bundle, FrozenHealthcheckBundle):
        raise ValueError('typed immutable frozen healthcheck bundle required')
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or not .05 <= seconds <= 250:
        raise ValueError('bounded frozen healthcheck duration required')
    with tempfile.TemporaryDirectory(prefix='owned-frozen-healthcheck-') as temporary:
        directory = Path(temporary)
        marker = {'schema_version': 1, 'is_mock': True, 'kind': 'owned_frozen_healthcheck', 'bundle_id': bundle.bundle_id}
        (directory / 'mock-frozen-healthcheck.json').write_text(canonical_json(marker), encoding='utf-8')
        (directory / 'input.json').write_text(bundle.serialized, encoding='utf-8')
        native = run_python_module('executors.local.frozen_healthcheck_child',
            ('--directory', str(directory), '--bundle-id', bundle.bundle_id), seconds=seconds,
            memory_limit_bytes=512 * 1024 * 1024, output_limit_bytes=65536)
        receipt = {key: value for key, value in native.items() if key != 'stdout'}
        if native.get('outcome') != 'completed' or native.get('is_child_stopped') is not True:
            code = native.get('outcome') if type(native.get('outcome')) is str else 'native_outcome_unknown'
            if code == 'completed':
                code = 'child_not_stopped'
            return FrozenHealthcheckExecution(bundle.bundle_id, bundle.code_sha, 'execution_failed', code, None, canonical_json(receipt))
        header = _header(bundle, native.get('stdout'))
        path = directory / 'result.json'
        if header['state'] == 'waiting_for_data':
            if path.exists() or path.is_symlink() or header['result_id'] is not None or type(header['result_size']) is not int or header['result_size'] != 0:
                raise ValueError('pending frozen child cannot return a result')
            pending = HealthcheckInputsPending(header['error_code'])
            return FrozenHealthcheckExecution(bundle.bundle_id, bundle.code_sha, 'waiting_for_data', pending.code, None, canonical_json(receipt))
        if (header['state'] != 'computed' or header['error_code'] is not None or type(header['result_size']) is not int
                or not 0 < header['result_size'] <= MAX_OBJECT_BYTES or path.is_symlink() or not path.is_file()
                or path.stat().st_size != header['result_size']):
            raise ValueError('computed frozen sidecar metadata differs')
        with path.open('rb') as stream:
            raw = stream.read(MAX_OBJECT_BYTES + 1)
        if len(raw) != header['result_size'] or hashlib.sha256(raw).hexdigest() != header['result_id']:
            raise ValueError('frozen sidecar integrity differs')
        serialized = raw.decode('utf-8')
        artifact = json.loads(serialized)
        if canonical_json(artifact) != serialized:
            raise ValueError('canonical frozen sidecar required')
        validate_frozen_healthcheck_artifact(bundle, artifact)
        return FrozenHealthcheckExecution(bundle.bundle_id, bundle.code_sha, 'computed', None, serialized, canonical_json(receipt))


def _header(bundle, serialized):
    if type(serialized) is not str or not 0 < len(serialized.encode('utf-8')) <= 65536:
        raise ValueError('bounded frozen child header required')
    value = json.loads(serialized)
    if (type(value) is not dict or set(value) != {'schema_version', 'is_mock', 'network_providers', 'bundle_id',
            'code_sha', 'state', 'error_code', 'result_id', 'result_size'}
            or type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['is_mock'] is not True or value['network_providers'] != []
            or value['bundle_id'] != bundle.bundle_id or value['code_sha'] != bundle.code_sha
            or canonical_json(value) != serialized.strip()):
        raise ValueError('frozen child header identity differs')
    return value
