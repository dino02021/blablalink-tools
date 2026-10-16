"""Pure bounded-file healthcheck child; no coordinator or provider access."""

import argparse
import hashlib
import json
import os
from pathlib import Path

from application.frozen_healthcheck import FrozenHealthcheckBundle, MAX_BUNDLE_BYTES, validate_frozen_healthcheck_artifact
from application.healthcheck_artifact import HealthcheckInputsPending, build_healthcheck_artifact
from domain.chunks import MAX_OBJECT_BYTES
from domain.snapshots import canonical_json


def main():
    parser = argparse.ArgumentParser(description='Pure mock frozen healthcheck calculation')
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--bundle-id', required=True)
    args = parser.parse_args()
    directory = args.directory.absolute()
    if any(path.is_symlink() for path in (directory, *directory.parents)) or not directory.is_dir():
        raise ValueError('owned frozen healthcheck directory required')
    marker = directory / 'mock-frozen-healthcheck.json'
    expected = {'schema_version': 1, 'is_mock': True, 'kind': 'owned_frozen_healthcheck', 'bundle_id': args.bundle_id}
    if marker.is_symlink() or not marker.is_file() or marker.stat().st_size > 4096 or marker.read_bytes() != canonical_json(expected).encode():
        raise ValueError('owned frozen healthcheck marker differs')
    source, target = directory / 'input.json', directory / 'result.json'
    if target.exists() or target.is_symlink():
        raise ValueError('new frozen result sidecar required')
    if source.is_symlink() or not source.is_file() or source.stat().st_size > MAX_BUNDLE_BYTES:
        raise ValueError('bounded frozen input required')
    with source.open('rb') as stream:
        raw = stream.read(MAX_BUNDLE_BYTES + 1)
    if len(raw) > MAX_BUNDLE_BYTES:
        raise ValueError('frozen input exceeds bound')
    bundle = FrozenHealthcheckBundle(raw.decode('utf-8'))
    if bundle.bundle_id != args.bundle_id:
        raise ValueError('frozen input identity differs')
    header = {'schema_version': 1, 'is_mock': True, 'network_providers': [], 'bundle_id': bundle.bundle_id,
              'code_sha': bundle.code_sha, 'state': 'computed', 'error_code': None, 'result_id': None, 'result_size': 0}
    try:
        artifact = build_healthcheck_artifact(*bundle.documents(), bundle.scope_id, bundle.code_sha)
    except HealthcheckInputsPending as error:
        header.update(state='waiting_for_data', error_code=error.code)
    else:
        validate_frozen_healthcheck_artifact(bundle, artifact)
        result = canonical_json(artifact).encode('utf-8')
        if not 0 < len(result) <= MAX_OBJECT_BYTES:
            raise ValueError('frozen result exceeds object limit')
        with target.open('xb') as output:
            output.write(result)
            output.flush()
            os.fsync(output.fileno())
        header.update(result_id=hashlib.sha256(result).hexdigest(), result_size=len(result))
    print(canonical_json(header))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
