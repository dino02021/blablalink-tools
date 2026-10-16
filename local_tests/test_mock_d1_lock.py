from concurrent.futures import ThreadPoolExecutor
import json
import subprocess
import sys
import threading

import pytest

from adapters.mock_d1_lock import mock_d1_access
from adapters.mock_d1_workspace import create_mock_d1_workspace
from executors.local.emulator_process import run_d1_emulator

SCHEMA = 'a' * 64


def test_shared_clients_overlap_and_maintenance_waits(tmp_path):
    directory = create_mock_d1_workspace(tmp_path / 'd1', SCHEMA)
    ready = threading.Barrier(3)
    release = threading.Event()
    def reader():
        with mock_d1_access(directory, SCHEMA):
            ready.wait(timeout=5)
            assert release.wait(timeout=5)
    with ThreadPoolExecutor(max_workers=2) as executor:
        readers = [executor.submit(reader) for _ in range(2)]
        ready.wait(timeout=5)
        try:
            with pytest.raises(TimeoutError):
                with mock_d1_access(directory, SCHEMA, is_exclusive=True, timeout=.05):
                    pytest.fail('maintenance overlapped SDK access')
        finally:
            release.set()
        for result in readers:
            result.result()
    with mock_d1_access(directory, SCHEMA, is_exclusive=True):
        with ThreadPoolExecutor(max_workers=1) as executor:
            def blocked():
                with pytest.raises(TimeoutError):
                    with mock_d1_access(directory, SCHEMA, timeout=.05):
                        pytest.fail('reader entered maintenance')
            executor.submit(blocked).result()


def test_lock_is_cross_process_and_os_releases_after_process_exit(tmp_path):
    directory = create_mock_d1_workspace(tmp_path / 'd1', SCHEMA)
    script = "from adapters.mock_d1_lock import mock_d1_access; import sys; "
    script += "\nwith mock_d1_access(sys.argv[1],sys.argv[2],is_exclusive=True):\n print('locked',flush=True)\n sys.stdin.readline()\n"
    process = subprocess.Popen([sys.executable, '-c', script, str(directory), SCHEMA], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == 'locked'
        with pytest.raises(TimeoutError):
            with mock_d1_access(directory, SCHEMA, timeout=.05):
                pytest.fail('process lock ignored')
    finally:
        process.kill()
        process.wait(timeout=5)
        process.stdin.close()
        process.stdout.close()
    with mock_d1_access(directory, SCHEMA, is_exclusive=True, timeout=.5):
        pass


def test_fence_appearing_while_waiting_is_rechecked(tmp_path):
    directory = create_mock_d1_workspace(tmp_path / 'd1', SCHEMA)
    entered = threading.Event()
    from adapters import mock_d1_lock
    original = mock_d1_lock._try_lock
    def wait_for_lock(handle, is_exclusive):
        entered.set()
        return original(handle, is_exclusive)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(mock_d1_lock, '_try_lock', wait_for_lock)
        with ThreadPoolExecutor(max_workers=1) as executor:
            with mock_d1_access(directory, SCHEMA, is_exclusive=True):
                entered.clear()
                future = executor.submit(lambda: _enter(directory))
                assert entered.wait(timeout=5)
                (directory / 'restore-lock.json').write_text('{}')
            with pytest.raises(ValueError, match='fenced'):
                future.result(timeout=5)


def _enter(directory):
    with mock_d1_access(directory, SCHEMA):
        return True


def test_exception_releases_lock_and_nested_access_is_rejected(tmp_path):
    directory = create_mock_d1_workspace(tmp_path / 'd1', SCHEMA)
    with pytest.raises(RuntimeError):
        with mock_d1_access(directory, SCHEMA, is_exclusive=True):
            with pytest.raises(ValueError, match='nested'):
                with mock_d1_access(directory, SCHEMA):
                    pass
            raise RuntimeError('mock interrupted maintenance')
    with mock_d1_access(directory, SCHEMA, is_exclusive=True):
        pass


@pytest.mark.parametrize('timeout', [0, -1, 61, float('inf'), float('nan'), True])
def test_invalid_wait_bounds_do_not_create_lock(tmp_path, timeout):
    directory = create_mock_d1_workspace(tmp_path / 'd1', SCHEMA)
    with pytest.raises(ValueError):
        with mock_d1_access(directory, SCHEMA, timeout=timeout):
            pass
    assert not (directory / '.sdk-access.lock').exists()


@pytest.mark.skipif(sys.platform != 'win32', reason='requires verified Windows SDK supervision')
def test_persistent_sdk_invocation_holds_shared_lock_through_disposal(tmp_path, monkeypatch):
    directory = create_mock_d1_workspace(tmp_path / 'd1', SCHEMA)
    from adapters.windows_child_job import WindowsChildJob
    original = WindowsChildJob.attach
    def attached(job, process):
        original(job, process)
        with ThreadPoolExecutor(max_workers=1) as executor:
            def maintenance():
                with pytest.raises(TimeoutError):
                    with mock_d1_access(directory, SCHEMA, is_exclusive=True, timeout=.05):
                        pytest.fail('SDK was not protected')
            executor.submit(maintenance).result()
    monkeypatch.setattr(WindowsChildJob, 'attach', attached)
    receipt = run_d1_emulator({'operations': []}, mock_directory=directory, schema_id=SCHEMA)
    assert receipt['outcome'] == 'completed'
    assert json.loads(receipt['stdout'])['are_sdk_children_closed'] is True
    with mock_d1_access(directory, SCHEMA, is_exclusive=True):
        pass
