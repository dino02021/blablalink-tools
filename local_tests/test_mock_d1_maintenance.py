from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import sys

import pytest

from adapters.local_d1_versioned_session import LocalD1VersionedSession
from adapters.mock_d1_lock import mock_d1_access
from adapters.mock_d1_maintenance import (MaintenanceIntent, maintenance_payload, maintenance_storage,
    mock_d1_maintenance_access)
from adapters.mock_d1_workspace import create_mock_d1_workspace, require_mock_d1_workspace
from coordination.d1_migrations import begin_migration
from coordination.d1_statement import D1Statement
from executors.local.emulator_process import run_d1_emulator, run_d1_maintenance_batch
from local_tests.test_d1_migrations import definition


def fixture(tmp_path):
    intent = MaintenanceIntent(definition(), 0, 1)
    directory = create_mock_d1_workspace(tmp_path / 'd1', intent.source_schema_id)
    return directory, intent


def authorize(capability, statements):
    return maintenance_storage(capability, capability.directory, capability.intent.source_schema_id,
                               maintenance_payload(statements))


def test_fixed_reads_precede_journal_and_writes_require_matching_fence(tmp_path):
    directory, intent = fixture(tmp_path)
    with mock_d1_maintenance_access(directory, intent) as capability:
        assert authorize(capability, intent.metadata_frame) == directory / 'storage'
        with pytest.raises(ValueError, match='journal'):
            authorize(capability, begin_migration(intent.plan, 0, 1))
        capability.fence()
        assert (directory / 'maintenance-lock.json').read_bytes() == intent.journal_bytes
        assert authorize(capability, begin_migration(intent.plan, 0, 1)) == directory / 'storage'
        with pytest.raises(ValueError, match='maintenance'):
            require_mock_d1_workspace(directory, intent.source_schema_id)
    with pytest.raises(ValueError, match='active'):
        authorize(capability, intent.metadata_frame)
    assert (directory / 'maintenance-lock.json').is_file()


def test_forged_cross_thread_changed_body_and_wrong_directory_grants_rejected(tmp_path):
    directory, intent = fixture(tmp_path)
    other = create_mock_d1_workspace(tmp_path / 'other', intent.source_schema_id)
    with mock_d1_maintenance_access(directory, intent) as capability:
        with pytest.raises(ValueError, match='active'):
            authorize(replace(capability), intent.metadata_frame)
        with ThreadPoolExecutor(max_workers=1) as executor:
            with pytest.raises(ValueError, match='active'):
                executor.submit(authorize, capability, intent.metadata_frame).result()
        with pytest.raises(ValueError, match='workspace'):
            maintenance_storage(capability, other, intent.source_schema_id, maintenance_payload(intent.metadata_frame))
        with pytest.raises(ValueError, match='fixed plan'):
            authorize(capability, (D1Statement('SELECT 2', ()),))


def test_exclusive_scope_blocks_other_sdk_readers_without_creating_journal(tmp_path):
    directory, intent = fixture(tmp_path)
    with mock_d1_maintenance_access(directory, intent):
        with ThreadPoolExecutor(max_workers=1) as executor:
            def reader():
                with mock_d1_access(directory, intent.source_schema_id, timeout=.05):
                    pytest.fail('reader entered active maintenance')
            with pytest.raises(TimeoutError):
                executor.submit(reader).result()
    assert not (directory / 'maintenance-lock.json').exists()
    assert require_mock_d1_workspace(directory, intent.source_schema_id) == directory


@pytest.mark.parametrize('content', [b'{}', b'not-json', b' ' * 4097])
def test_unmatched_journal_is_never_overwritten(tmp_path, content):
    directory, intent = fixture(tmp_path)
    journal = directory / 'maintenance-lock.json'
    journal.write_bytes(content)
    marker = (directory / 'mock-d1.json').read_bytes()
    with pytest.raises(ValueError, match='maintenance'):
        with mock_d1_maintenance_access(directory, intent):
            pytest.fail('unmatched journal accepted')
    assert journal.read_bytes() == content
    assert (directory / 'mock-d1.json').read_bytes() == marker


def test_same_intent_reenters_fence_but_restore_lock_always_wins(tmp_path):
    directory, intent = fixture(tmp_path)
    with mock_d1_maintenance_access(directory, intent) as first:
        first.fence()
    with mock_d1_maintenance_access(directory, intent) as second:
        authorize(second, intent.metadata_frame)
        with pytest.raises(ValueError, match='active'):
            authorize(first, intent.metadata_frame)
        (directory / 'restore-lock.json').write_text('{}')
        with pytest.raises(ValueError, match='restore'):
            authorize(second, intent.metadata_frame)
    with pytest.raises(ValueError, match='restore'):
        with mock_d1_maintenance_access(directory, intent):
            pass


def test_missing_capability_and_stale_scope_never_start_a_child(tmp_path, monkeypatch):
    import executors.local.emulator_process as emulator
    def forbidden(*args, **kwargs):
        pytest.fail('ungranted SDK child started')
    monkeypatch.setattr(emulator.subprocess, 'Popen', forbidden)
    directory, intent = fixture(tmp_path)
    with pytest.raises(ValueError, match='active'):
        run_d1_maintenance_batch(intent.metadata_frame, None)
    with mock_d1_maintenance_access(directory, intent) as capability:
        with pytest.raises(ValueError, match='nested'):
            run_d1_emulator({'schema_statements': [], 'operations': []}, mock_directory=directory, schema_id=intent.source_schema_id)
        capability.fence()
        with pytest.raises(ValueError, match='maintenance'):
            run_d1_emulator({'schema_statements': [], 'operations': []}, mock_directory=directory, schema_id=intent.source_schema_id)
    with pytest.raises(ValueError, match='active'):
        run_d1_maintenance_batch(intent.metadata_frame, capability)


@pytest.mark.skipif(sys.platform != 'win32', reason='requires verified Windows SDK supervision')
def test_actual_sdk_uses_granted_metadata_and_step_frames_without_shared_lock_bypass(tmp_path):
    intent = MaintenanceIntent(definition(), 0, 1)
    directory = tmp_path / 'd1'
    LocalD1VersionedSession(directory, intent.plan, 0, 1, is_create=True)
    with mock_d1_maintenance_access(directory, intent) as capability:
        receipt = run_d1_maintenance_batch(intent.metadata_frame, capability)
        result = json.loads(receipt['stdout'])
        assert receipt['outcome'] == 'completed' and result['are_sdk_children_closed'] is True
        assert result['operations'][0]['outcome'] == 'completed'
        assert result['operations'][0]['results'][1]['results'][0]['state'] == 'ready'
        with pytest.raises(ValueError, match='journal'):
            run_d1_maintenance_batch(begin_migration(intent.plan, 0, 1), capability)
        capability.fence()
        receipt = run_d1_maintenance_batch(begin_migration(intent.plan, 0, 1), capability)
        result = json.loads(receipt['stdout'])
        assert result['operations'][0]['outcome'] == 'completed'
        metadata = json.loads(run_d1_maintenance_batch(intent.metadata_frame, capability)['stdout'])
        assert metadata['operations'][0]['results'][1]['results'][0]['state'] == 'migrating'
    with pytest.raises(ValueError, match='maintenance'):
        LocalD1VersionedSession(directory, intent.plan, 0, 1)
