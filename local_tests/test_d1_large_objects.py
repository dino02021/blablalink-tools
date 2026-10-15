import random
import sys
from pathlib import Path

import pytest

from adapters.local_d1_objects import LocalD1Objects
from coordination.d1_objects import SCHEMA_ID
from domain.chunks import MAX_OBJECT_BYTES, encode_value
from executors.local.emulator_process import run_d1_object_session
from adapters.mock_d1_workspace import create_mock_d1_workspace


@pytest.mark.skipif(sys.platform != 'win32', reason='requires verified Windows SDK process supervision')
def test_actual_sixteen_mib_canonical_object_upload_restart_seal_and_read(tmp_path):
    value = random.Random(106).randbytes((MAX_OBJECT_BYTES - 2) // 2).hex()
    manifest, chunks = encode_value(value)
    assert manifest.raw_size == MAX_OBJECT_BYTES and len(chunks) > 200
    directory = tmp_path / 'd1'
    store = LocalD1Objects(directory, is_create=True)
    store.begin(manifest)
    # Resume after an independent first chunk invocation.
    store.put(manifest, 0, chunks[0])
    restarted = LocalD1Objects(directory)
    assert 0 not in restarted.missing(manifest)
    restarted.put_many(manifest, chunks)
    assert restarted.seal(manifest) == value
    assert LocalD1Objects(directory).read(manifest.object_id) == value


@pytest.mark.parametrize('payload', [
    {'schema_statements': [], 'operations': []},
    {'schema_statements': [], 'operations': [{'kind': 'query'}]},
    {'schema_statements': [], 'operations': [{'kind': 'batch', 'statements': []}]},
    {'schema_statements': [], 'operations': [{'kind': 'batch', 'statements': [{'sql': 'SELECT ?', 'parameters': ['x' * 65536]}]}]},
], ids=['empty', 'wrong-operation', 'empty-batch', 'oversize-frame'])
def test_session_rejects_unbounded_or_ambiguous_frames_before_sdk(tmp_path, payload):
    with pytest.raises(ValueError):
        run_d1_object_session(payload, mock_directory=tmp_path, schema_id=SCHEMA_ID)


@pytest.mark.skipif(sys.platform != 'win32', reason='requires verified Windows SDK process supervision')
def test_owned_session_reply_digest_is_checked_before_consuming_results(tmp_path, monkeypatch):
    directory = create_mock_d1_workspace(tmp_path / 'd1', SCHEMA_ID)
    original = Path.read_bytes
    def read_bytes(path):
        return b'{}' if path.name == 'reply.json' else original(path)
    monkeypatch.setattr(Path, 'read_bytes', read_bytes)
    with pytest.raises(ValueError, match='reply integrity differs'):
        run_d1_object_session({'schema_statements': [], 'operations': [
            {'kind': 'batch', 'statements': [{'sql': 'SELECT 1', 'parameters': []}]},
        ]}, mock_directory=directory, schema_id=SCHEMA_ID)


@pytest.mark.skipif(sys.platform != 'win32', reason='requires verified Windows SDK process supervision')
def test_successful_multi_chunk_write_with_lost_reply_can_replay_without_sealing(tmp_path, monkeypatch):
    import adapters.local_d1_objects as module
    manifest, chunks = encode_value(random.Random(107).randbytes(40000).hex())
    assert len(chunks) > 1
    directory = tmp_path / 'd1'
    store = LocalD1Objects(directory, is_create=True)
    store.begin(manifest)
    original = module.run_d1_object_session
    def lose_reply(*args, **kwargs):
        receipt = original(*args, **kwargs)
        assert receipt['outcome'] == 'completed'
        raise ValueError('simulated reply lost after committed frames')
    monkeypatch.setattr(module, 'run_d1_object_session', lose_reply)
    with pytest.raises(ValueError, match='reply lost'):
        store.put_many(manifest, chunks)
    monkeypatch.setattr(module, 'run_d1_object_session', original)
    restarted = LocalD1Objects(directory)
    assert restarted.missing(manifest) == ()
    with pytest.raises(ValueError):
        restarted.read(manifest.object_id)
    restarted.put_many(manifest, chunks)
    assert restarted.seal(manifest) == restarted.read(manifest.object_id)
