"""Explicitly marked local Miniflare storage; never a hosted D1 binding."""

import json
from pathlib import Path
import re

from domain.snapshots import canonical_json


def create_mock_d1_workspace(directory, schema_id):
    marker = _marker(schema_id)
    directory = Path(directory).absolute()
    if directory.exists() or directory.is_symlink():
        raise ValueError('new mock D1 directory required')
    if any(parent.is_symlink() for parent in directory.parents):
        raise ValueError('mock D1 symlink ancestor rejected')
    directory.mkdir(parents=True)
    (directory / 'mock-d1.json').write_text(canonical_json(marker), encoding='utf-8')
    (directory / 'storage').mkdir()
    return require_mock_d1_workspace(directory, schema_id)


def require_mock_d1_workspace(directory, schema_id):
    return _inspect_mock_d1_workspace(directory, (_marker(schema_id),))


def _inspect_mock_d1_workspace(directory, markers, maintenance_bytes=None):
    directory = Path(directory).absolute()
    if any(path.is_symlink() for path in (directory, *directory.parents)):
        raise ValueError('mock D1 symlink rejected')
    if (directory / 'restore-lock.json').exists() or (directory / 'restore-lock.json').is_symlink():
        raise ValueError('mock D1 restore is fenced')
    journal = directory / 'maintenance-lock.json'
    if journal.is_symlink() or (journal.exists() and (maintenance_bytes is None
            or not journal.is_file() or journal.stat().st_size > 4096 or journal.read_bytes() != maintenance_bytes)):
        raise ValueError('mock D1 maintenance is fenced')
    path = directory / 'mock-d1.json'
    storage = directory / 'storage'
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
        raise ValueError('mock D1 marker required')
    if canonical_json(json.loads(path.read_text(encoding='utf-8'))) not in {canonical_json(marker) for marker in markers}:
        raise ValueError('mock D1 schema marker differs')
    if storage.is_symlink() or not storage.is_dir():
        raise ValueError('mock D1 storage directory required')
    # The caller owns this offline directory; reject redirected existing files.
    if any(child.is_symlink() for child in storage.rglob('*')):
        raise ValueError('mock D1 redirected storage rejected')
    return directory.resolve()


def _marker(schema_id):
    if type(schema_id) is not str or re.fullmatch('[0-9a-f]{64}', schema_id) is None:
        raise ValueError('mock D1 schema digest required')
    return {'schema_version': 1, 'storage_kind': 'miniflare_only', 'is_mock': True, 'schema_id': schema_id}
