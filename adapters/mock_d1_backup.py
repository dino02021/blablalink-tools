"""Bounded, drained Miniflare-only snapshots and permanently fenced clones."""

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import zipfile

from adapters.mock_d1_lock import mock_d1_access
from adapters.mock_d1_workspace import require_mock_d1_workspace
from domain.snapshots import canonical_json

MAX_BYTES = 64 * 1024 * 1024
MAX_ENTRIES = 1000
MAX_MANIFEST = 512 * 1024


def create_mock_d1_backup(directory, archive, schema_id):
    directory = require_mock_d1_workspace(directory, schema_id)
    archive = _safe_path(archive)
    if archive.exists() or archive.is_relative_to(directory):
        raise ValueError('new backup outside mock D1 workspace required')
    with mock_d1_access(directory, schema_id, is_exclusive=True):
        files = {'mock-d1.json': _read(directory / 'mock-d1.json', 4096)}
        inventory = _storage_files(directory)
        total = len(files['mock-d1.json'])
        for name in inventory:
            content = _read(directory / name, MAX_BYTES - total)
            total += len(content)
            files[name] = content
        manifest = {'schema_version': 1, 'is_mock': True, 'storage_kind': 'miniflare_only',
                    'schema_id': schema_id, 'files': {
                        name: {'size': len(content), 'sha256': _hash(content)} for name, content in sorted(files.items())}}
        serialized = canonical_json(manifest).encode('utf-8')
        if len(serialized) > MAX_MANIFEST:
            raise ValueError('mock D1 backup manifest exceeds bound')
        if inventory != _storage_files(directory) or any(_read(directory / name, len(content)) != content for name, content in files.items()):
            raise ValueError('mock D1 storage changed during capture')
        with archive.open('xb') as handle:
            with zipfile.ZipFile(handle, 'w', compression=zipfile.ZIP_STORED) as output:
                for name, content in files.items():
                    output.writestr(name, content)
                output.writestr('manifest.json', serialized)
    return {'is_mock': True, 'backup_id': _hash(serialized), 'schema_id': schema_id,
            'file_count': len(files), 'payload_bytes': total, 'archive': str(archive)}


def restore_mock_d1_backup(archive, directory, expected_backup_id, schema_id):
    archive, directory = _safe_path(archive), _safe_path(directory)
    if directory.exists():
        raise ValueError('new mock D1 restore destination required')
    if type(expected_backup_id) is not str or re.fullmatch('[0-9a-f]{64}', expected_backup_id) is None:
        raise ValueError('mock D1 backup digest required')
    if type(schema_id) is not str or re.fullmatch('[0-9a-f]{64}', schema_id) is None:
        raise ValueError('mock D1 schema digest required')
    if not archive.is_file() or archive.stat().st_size > MAX_BYTES + MAX_MANIFEST + 1024 * 1024:
        raise ValueError('bounded mock D1 archive required')
    with zipfile.ZipFile(archive) as source:
        infos = source.infolist()
        names = [info.filename for info in infos]
        folded = {name.casefold() for name in names}
        if (not 2 <= len(names) <= MAX_ENTRIES + 2 or len(names) != len(set(names))
                or len(folded) != len(names) or 'manifest.json' not in names or 'mock-d1.json' not in names
                or any(info.compress_type != zipfile.ZIP_STORED or info.is_dir() for info in infos)
                or any(name not in ('manifest.json', 'mock-d1.json') and not _is_storage_name(name) for name in names)
                or sum(info.file_size for info in infos) > MAX_BYTES + MAX_MANIFEST):
            raise ValueError('mock D1 archive inventory differs')
        if any('/'.join(name.split('/')[:index]).casefold() in folded
               for name in names for index in range(1, len(name.split('/')))):
            raise ValueError('mock D1 archive file and directory collide')
        serialized = _zip_read(source, 'manifest.json', MAX_MANIFEST)
        if _hash(serialized) != expected_backup_id:
            raise ValueError('mock D1 backup identity differs')
        manifest = json.loads(serialized)
        if (type(manifest) is not dict or canonical_json(manifest).encode('utf-8') != serialized
                or set(manifest) != {'schema_version', 'is_mock', 'storage_kind', 'schema_id', 'files'}
                or type(manifest['schema_version']) is not int or manifest['schema_version'] != 1
                or manifest['is_mock'] is not True or manifest['storage_kind'] != 'miniflare_only'
                or manifest['schema_id'] != schema_id or type(manifest['files']) is not dict
                or set(manifest['files']) != set(names) - {'manifest.json'}):
            raise ValueError('mock D1 backup schema or manifest differs')
        files = {}
        total = 0
        for name, metadata in manifest['files'].items():
            if (type(metadata) is not dict or set(metadata) != {'size', 'sha256'}
                    or type(metadata['size']) is not int or not 0 <= metadata['size'] <= MAX_BYTES - total):
                raise ValueError('mock D1 file metadata differs')
            content = _zip_read(source, name, metadata['size'])
            if len(content) != metadata['size'] or _hash(content) != metadata['sha256']:
                raise ValueError('mock D1 file integrity differs')
            files[name] = content
            total += len(content)
    marker = {'schema_version': 1, 'storage_kind': 'miniflare_only', 'is_mock': True, 'schema_id': schema_id}
    marker_bytes = files['mock-d1.json']
    if len(marker_bytes) > 4096 or canonical_json(json.loads(marker_bytes)) != canonical_json(marker):
        raise ValueError('mock D1 backup marker differs')
    directory.mkdir(parents=True, exist_ok=False)
    # First artifact is a fence: interrupted restores must never initialize writers.
    fence = {'schema_version': 1, 'is_mock': True, 'backup_id': expected_backup_id,
             'schema_id': schema_id, 'execution_mode': 'restored_read_only'}
    (directory / 'restore-lock.json').write_text(canonical_json(fence), encoding='utf-8')
    (directory / 'storage').mkdir()
    for name, content in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as output:
            output.write(content)
    return {'is_mock': True, 'backup_id': expected_backup_id, 'schema_id': schema_id,
            'execution_mode': 'restored_read_only', 'workspace': str(directory)}


def _safe_path(value):
    path = Path(value).absolute()
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError('mock D1 backup path cannot be redirected')
    return path


def _storage_files(directory):
    files = []
    for index, path in enumerate((directory / 'storage').rglob('*')):
        if index >= MAX_ENTRIES or path.is_symlink():
            raise ValueError('bounded unredirected mock D1 inventory required')
        if path.is_file():
            name = path.relative_to(directory).as_posix()
            if not _is_storage_name(name):
                raise ValueError('mock D1 storage path differs')
            files.append(name)
        elif not path.is_dir():
            raise ValueError('regular mock D1 storage required')
    if len({name.casefold() for name in files}) != len(files):
        raise ValueError('mock D1 storage paths differ only by case')
    return sorted(files)


def _is_storage_name(name):
    if type(name) is not str or not 1 <= len(name) <= 240 or '\\' in name or ':' in name:
        return False
    parts = PurePosixPath(name).parts
    reserved = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{index}' for index in range(1, 10)),
                *(f'LPT{index}' for index in range(1, 10))}
    return (2 <= len(parts) <= 16 and parts[0] == 'storage' and '/'.join(parts) == name
            and all(re.fullmatch('[A-Za-z0-9_.-]+', part) and part not in ('.', '..')
                    and not part.endswith(('.', ' ')) and part.split('.')[0].upper() not in reserved for part in parts))


def _read(path, limit):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ValueError('mock D1 snapshot size or file differs')
    with path.open('rb') as source:
        content = source.read(limit + 1)
    if len(content) > limit:
        raise ValueError('mock D1 snapshot exceeds bound')
    return content


def _zip_read(source, name, limit):
    if source.getinfo(name).file_size > limit:
        raise ValueError('mock D1 archive entry exceeds bound')
    with source.open(name) as handle:
        content = handle.read(limit + 1)
    if len(content) > limit:
        raise ValueError('mock D1 archive entry exceeds bound')
    return content


def _hash(content):
    return hashlib.sha256(content).hexdigest()
