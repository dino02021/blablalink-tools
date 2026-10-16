from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import sys
import zipfile

import pytest

from adapters.mock_d1_backup import create_mock_d1_backup, restore_mock_d1_backup
from adapters.mock_d1_lock import mock_d1_access
from adapters.mock_d1_workspace import create_mock_d1_workspace, require_mock_d1_workspace
from adapters.local_d1_session import LocalD1Session
from coordination.d1_schema import D1Schema
from coordination.d1_statement import D1Statement

SCHEMA = 'a' * 64


def workspace(tmp_path):
    directory = create_mock_d1_workspace(tmp_path / 'd1', SCHEMA)
    (directory / 'storage' / 'nested').mkdir()
    (directory / 'storage' / 'nested' / 'mock.sqlite').write_bytes(b'mock physical payload')
    return directory


def test_roundtrip_preserves_all_bytes_and_clone_is_fenced(tmp_path):
    directory = workspace(tmp_path)
    archive = tmp_path / 'backup.zip'
    first = create_mock_d1_backup(directory, archive, SCHEMA)
    second = create_mock_d1_backup(directory, tmp_path / 'backup2.zip', SCHEMA)
    assert first['backup_id'] == second['backup_id']
    clone = tmp_path / 'clone'
    result = restore_mock_d1_backup(archive, clone, first['backup_id'], SCHEMA)
    assert result['execution_mode'] == 'restored_read_only'
    assert (clone / 'storage/nested/mock.sqlite').read_bytes() == b'mock physical payload'
    assert not (clone / '.sdk-access.lock').exists()
    with pytest.raises(ValueError, match='fenced'):
        require_mock_d1_workspace(clone, SCHEMA)
    assert require_mock_d1_workspace(directory, SCHEMA) == directory.resolve()


def test_workspace_accepted_indented_marker_roundtrips_without_changing_bytes(tmp_path):
    directory = workspace(tmp_path)
    marker = directory / 'mock-d1.json'
    marker.write_text(json.dumps(json.loads(marker.read_text()), indent=2), encoding='utf-8')
    original = marker.read_bytes()
    require_mock_d1_workspace(directory, SCHEMA)
    receipt = create_mock_d1_backup(directory, tmp_path / 'backup.zip', SCHEMA)
    restore_mock_d1_backup(tmp_path / 'backup.zip', tmp_path / 'clone', receipt['backup_id'], SCHEMA)
    assert (tmp_path / 'clone/mock-d1.json').read_bytes() == original


def test_capture_holds_exclusive_lock_until_archive_completed(tmp_path, monkeypatch):
    directory = workspace(tmp_path)
    original = zipfile.ZipFile.writestr
    checked = []
    def write(output, name, content, *args, **kwargs):
        with ThreadPoolExecutor(max_workers=1) as executor:
            def reader():
                with pytest.raises(TimeoutError):
                    with mock_d1_access(directory, SCHEMA, timeout=.05):
                        pytest.fail('backup did not drain SDK writers')
            executor.submit(reader).result()
        checked.append(name)
        return original(output, name, content, *args, **kwargs)
    monkeypatch.setattr(zipfile.ZipFile, 'writestr', write)
    create_mock_d1_backup(directory, tmp_path / 'backup.zip', SCHEMA)
    assert 'manifest.json' in checked


def rewrite(archive, transform):
    with zipfile.ZipFile(archive) as source:
        files = {name: source.read(name) for name in source.namelist()}
    transform(files)
    with zipfile.ZipFile(archive, 'w') as target:
        for name, content in files.items():
            target.writestr(name, content)


@pytest.mark.parametrize('change', ['content', 'identity', 'schema', 'traversal', 'case', 'parent', 'reserved'])
def test_corrupt_or_ambiguous_archives_rejected_before_destination_creation(tmp_path, change):
    directory = workspace(tmp_path)
    archive = tmp_path / 'backup.zip'
    receipt = create_mock_d1_backup(directory, archive, SCHEMA)
    identity, schema = receipt['backup_id'], SCHEMA
    if change == 'identity':
        identity = 'b' * 64
    elif change == 'schema':
        schema = 'b' * 64
    else:
        key = {'content': 'storage/nested/mock.sqlite', 'traversal': 'storage/../escape',
               'case': 'storage/nested/MOCK.sqlite', 'parent': 'storage/nested', 'reserved': 'storage/CON.txt'}[change]
        rewrite(archive, lambda files: files.update({key: b'changed'}))
    clone = tmp_path / 'clone'
    with pytest.raises(ValueError):
        restore_mock_d1_backup(archive, clone, identity, schema)
    assert not clone.exists()


def test_interrupted_restore_leaves_fence_before_any_payload(tmp_path, monkeypatch):
    directory = workspace(tmp_path)
    archive = tmp_path / 'backup.zip'
    receipt = create_mock_d1_backup(directory, archive, SCHEMA)
    clone = tmp_path / 'clone'
    original = Path.open
    def open_path(path, mode='r', *args, **kwargs):
        if path.is_relative_to(clone) and mode == 'xb':
            assert (clone / 'restore-lock.json').is_file()
            raise OSError('mock disk failure')
        return original(path, mode, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', open_path)
    with pytest.raises(OSError, match='disk failure'):
        restore_mock_d1_backup(archive, clone, receipt['backup_id'], SCHEMA)
    assert (clone / 'restore-lock.json').is_file()
    with pytest.raises(ValueError, match='fenced'):
        require_mock_d1_workspace(clone, SCHEMA)


def test_backup_and_restore_never_overwrite_and_reject_oversize(tmp_path, monkeypatch):
    from adapters import mock_d1_backup
    directory = workspace(tmp_path)
    archive = tmp_path / 'backup.zip'
    receipt = create_mock_d1_backup(directory, archive, SCHEMA)
    before = archive.read_bytes()
    with pytest.raises(ValueError):
        create_mock_d1_backup(directory, archive, SCHEMA)
    assert archive.read_bytes() == before
    with pytest.raises(ValueError):
        create_mock_d1_backup(directory, directory / 'backup.zip', SCHEMA)
    with pytest.raises(ValueError):
        restore_mock_d1_backup(archive, directory, receipt['backup_id'], SCHEMA)
    monkeypatch.setattr(mock_d1_backup, 'MAX_BYTES', 10)
    with pytest.raises(ValueError):
        create_mock_d1_backup(directory, tmp_path / 'large.zip', SCHEMA)
    assert not (tmp_path / 'large.zip').exists()


@pytest.mark.skipif(sys.platform != 'win32', reason='requires verified Windows SDK supervision')
def test_real_sdk_storage_snapshot_contains_committed_rows_and_restore_refuses_sdk(tmp_path):
    schema = D1Schema(('CREATE TABLE mock_values(id TEXT PRIMARY KEY,value TEXT NOT NULL) WITHOUT ROWID',))
    session = LocalD1Session(tmp_path / 'real-d1', schema, is_create=True)
    session.execute((D1Statement('INSERT INTO mock_values VALUES(?,?)', ('mock-id', 'frozen result')),))
    archive = tmp_path / 'real-backup.zip'
    receipt = create_mock_d1_backup(session.directory, archive, schema.schema_id)
    clone = tmp_path / 'real-clone'
    restore_mock_d1_backup(archive, clone, receipt['backup_id'], schema.schema_id)
    with pytest.raises(ValueError, match='fenced'):
        LocalD1Session(clone, schema)
    databases = list((clone / 'storage').rglob('*.sqlite'))
    assert databases
    observed = []
    for database in databases:
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as connection:
            assert connection.execute('PRAGMA quick_check').fetchone() == ('ok',)
            if connection.execute("SELECT 1 FROM sqlite_master WHERE name='mock_values'").fetchone():
                observed.extend(connection.execute('SELECT id,value FROM mock_values').fetchall())
    assert observed == [('mock-id', 'frozen result')]
    result = session.execute((D1Statement('SELECT value FROM mock_values', ()),))
    assert result[0]['results'] == [{'value': 'frozen result'}]
