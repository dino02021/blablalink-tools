"""Bounded, content-addressed JSON snapshot transport; no pickle or network."""

from __future__ import annotations

import hashlib
import json
import zlib
from dataclasses import asdict, dataclass

from domain.snapshots import Document, canonical_json, restore_document

CHUNK_BYTES = 32 * 1024
MAX_OBJECT_BYTES = 16 * 1024 * 1024
MAX_CHUNKS = 512


@dataclass(frozen=True)
class ChunkManifest:
    object_id: str
    content_hash: str
    raw_size: int
    compressed_size: int
    chunk_hashes: tuple[str, ...]
    codec: str = "zlib"
    schema_version: int = 1

    def __post_init__(self):
        hashes = tuple(self.chunk_hashes)
        object.__setattr__(self, "chunk_hashes", hashes)
        for value in (self.object_id, self.content_hash, *hashes):
            if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise ValueError("invalid SHA-256")
        if self.object_id != self.content_hash:
            raise ValueError("snapshot identity must match content hash")
        if self.codec != "zlib" or type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("unsupported chunk schema or codec")
        for size in (self.raw_size, self.compressed_size):
            if type(size) is not int or not 0 < size <= MAX_OBJECT_BYTES:
                raise ValueError("invalid object size")
        if not 1 <= len(hashes) <= MAX_CHUNKS or len(hashes) != (self.compressed_size + CHUNK_BYTES - 1) // CHUNK_BYTES:
            raise ValueError("invalid chunk count")

    @property
    def serialized(self) -> str:
        return canonical_json(asdict(self))


def encode_document(document: Document) -> tuple[ChunkManifest, tuple[bytes, ...]]:
    return _encode_raw(document.serialized.encode("utf-8"))


def encode_value(value) -> tuple[ChunkManifest, tuple[bytes, ...]]:
    """Retain the canonical JSON identity used by coordinator result objects."""
    return _encode_raw(canonical_json(value).encode("utf-8"))


def verify_chunk(manifest: ChunkManifest, index: int, chunk: bytes) -> None:
    if type(index) is not int or not 0 <= index < len(manifest.chunk_hashes):
        raise ValueError("invalid chunk index")
    expected_size = min(CHUNK_BYTES, manifest.compressed_size - index * CHUNK_BYTES)
    if not isinstance(chunk, bytes) or len(chunk) != expected_size or _hash(chunk) != manifest.chunk_hashes[index]:
        raise ValueError("chunk integrity mismatch")


def decode_document(manifest: ChunkManifest, chunks: tuple[bytes, ...]) -> Document:
    return restore_document(_decode_raw(manifest, chunks).decode("utf-8"), manifest.object_id)


def decode_value(manifest: ChunkManifest, chunks: tuple[bytes, ...]):
    raw = _decode_raw(manifest, chunks)
    try:
        value = json.loads(raw)
        if canonical_json(value).encode("utf-8") != raw:
            raise ValueError("object JSON is not canonical")
    except (UnicodeError, json.JSONDecodeError, OverflowError, RecursionError) as error:
        raise ValueError("invalid canonical object JSON") from error
    return value


def _encode_raw(raw: bytes) -> tuple[ChunkManifest, tuple[bytes, ...]]:
    if len(raw) > MAX_OBJECT_BYTES:
        raise ValueError("snapshot too large")
    compressed = zlib.compress(raw)
    chunks = tuple(compressed[i:i + CHUNK_BYTES] for i in range(0, len(compressed), CHUNK_BYTES))
    manifest = ChunkManifest(_hash(raw), _hash(raw), len(raw), len(compressed), tuple(_hash(c) for c in chunks))
    return manifest, chunks


def _decode_raw(manifest: ChunkManifest, chunks: tuple[bytes, ...]) -> bytes:
    if len(chunks) != len(manifest.chunk_hashes):
        raise ValueError("incomplete snapshot")
    for index, chunk in enumerate(chunks):
        verify_chunk(manifest, index, chunk)
    decoder = zlib.decompressobj()
    try:
        raw = decoder.decompress(b"".join(chunks), manifest.raw_size + 1)
    except zlib.error as error:
        raise ValueError("invalid compressed snapshot") from error
    if (len(raw) != manifest.raw_size or not decoder.eof or decoder.unused_data
            or decoder.unconsumed_tail or _hash(raw) != manifest.content_hash):
        raise ValueError("snapshot size, stream or hash mismatch")
    return raw


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
