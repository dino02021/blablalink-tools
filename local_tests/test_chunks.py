import hashlib
import random
import zlib
from dataclasses import replace

import pytest

from adapters.local_chunks import LocalChunkStore
from adapters.local_state import LocalStateStore
from application.tasks import StateConflict
from domain.chunks import CHUNK_BYTES, ChunkManifest, decode_document, encode_document
from domain.snapshots import capture_document


def test_multichunk_resume_seal_and_read_after_restart(tmp_path):
    random_source = random.Random(42)
    value = random_source.randbytes(160000).hex()
    document = _document(value)
    manifest, chunks = encode_document(document)
    assert len(chunks) > 2 and all(len(chunk) <= CHUNK_BYTES for chunk in chunks)
    path = tmp_path / "chunks.sqlite"
    store = LocalChunkStore(path)
    store.begin(manifest)
    store.put(manifest.object_id, 1, chunks[1])
    with pytest.raises(StateConflict):
        store.read(manifest.object_id)
    with pytest.raises(StateConflict):
        store.seal(manifest.object_id)
    store.close()
    store = LocalChunkStore(path)
    try:
        assert 1 not in store.missing(manifest.object_id)
        for index, chunk in reversed(tuple(enumerate(chunks))):
            store.put(manifest.object_id, index, chunk)
            store.put(manifest.object_id, index, chunk)
        assert store.missing(manifest.object_id) == ()
        assert store.seal(manifest.object_id) == document
        assert store.read(manifest.object_id) == document
        assert store.seal(manifest.object_id) == document
    finally:
        store.close()


@pytest.mark.parametrize("change", ["missing", "corrupt", "trailing", "size", "hash"])
def test_transport_rejects_corruption_truncation_and_inconsistent_manifest(change):
    manifest, chunks = encode_document(_document("角色資料" * 500))
    if change == "missing":
        chunks = ()
    elif change == "corrupt":
        chunks = (bytes([chunks[0][0] ^ 1]) + chunks[0][1:],)
    elif change == "trailing":
        chunks = (chunks[0] + b"extra",)
        manifest = replace(manifest, compressed_size=len(chunks[0]), chunk_hashes=(_hash(chunks[0]),))
    elif change == "size":
        manifest = replace(manifest, raw_size=manifest.raw_size - 1)
    else:
        manifest = replace(manifest, content_hash="0" * 64, object_id="0" * 64)
    with pytest.raises(ValueError):
        decode_document(manifest, chunks)


def test_decompression_limit_prevents_trusting_declared_small_size():
    chunk = zlib.compress(b"x" * 1000000)
    manifest = ChunkManifest("0" * 64, "0" * 64, 1, len(chunk), (_hash(chunk),))
    with pytest.raises(ValueError):
        decode_document(manifest, (chunk,))


@pytest.mark.parametrize("field,value", [("codec", "pickle"), ("raw_size", True), ("raw_size", 0),
                                         ("compressed_size", 17000000), ("chunk_hashes", ()),
                                         ("schema_version", True), ("object_id", "../bad")])
def test_invalid_manifest(field, value):
    manifest, _ = encode_document(_document("demo"))
    with pytest.raises(ValueError):
        replace(manifest, **{field: value})


def test_persisted_chunk_corruption_is_detected_on_every_read(tmp_path):
    manifest, chunks = encode_document(_document("demo"))
    store = LocalChunkStore(tmp_path / "chunks.sqlite")
    try:
        store.begin(manifest)
        store.put(manifest.object_id, 0, chunks[0])
        store.seal(manifest.object_id)
        with store.connection:
            store.connection.execute("UPDATE object_chunks SET content=?", (b"corrupted",))
        with pytest.raises(ValueError):
            store.read(manifest.object_id)
    finally:
        store.close()


def test_only_sealed_verified_snapshot_is_promoted_to_state_store(tmp_path):
    document = _document("mock_data")
    manifest, chunks = encode_document(document)
    staging = LocalChunkStore(tmp_path / "staging.sqlite")
    state = LocalStateStore(tmp_path / "state.sqlite")
    try:
        staging.begin(manifest)
        with pytest.raises(StateConflict):
            staging.read(manifest.object_id)
        for index, chunk in enumerate(chunks):
            staging.put(manifest.object_id, index, chunk)
        staging.seal(manifest.object_id)
        object_id = state.put_document(staging.read(manifest.object_id))
        assert state.get_document(object_id) == document
        with pytest.raises(StateConflict):
            staging.begin(replace(manifest, raw_size=manifest.raw_size + 1))
    finally:
        staging.close()
        state.close()


def _document(value):
    return capture_document("raw", "union_demo", "2026-10-15T00:00:00Z", "reference_demo", {"synthetic": value})


def _hash(value):
    return hashlib.sha256(value).hexdigest()
