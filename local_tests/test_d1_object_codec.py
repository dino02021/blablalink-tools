import hashlib
import json
import random
import zlib

import pytest

from coordination.d1_binary import D1Binary
from domain.chunks import ChunkManifest, MAX_OBJECT_BYTES, decode_value, encode_value
from domain.snapshots import canonical_json


@pytest.mark.parametrize('value', [None, False, 0, '', [], {}, {'角色': [0, None, '技能', 1.25]}])
def test_generic_chunk_identity_matches_coordinator_canonical_objects(value):
    manifest, chunks = encode_value(value)
    assert manifest.object_id == hashlib.sha256(canonical_json(value).encode()).hexdigest()
    assert decode_value(manifest, chunks) == value


@pytest.mark.parametrize('raw', [b'{"b":1,"a":2}', b'{"a":1,"a":2}', b'NaN', b'\xff', b'1  '])
def test_valid_compression_does_not_bypass_canonical_json_validation(raw):
    chunk = zlib.compress(raw)
    digest = hashlib.sha256(raw).hexdigest()
    manifest = ChunkManifest(digest, digest, len(raw), len(chunk), (hashlib.sha256(chunk).hexdigest(),))
    with pytest.raises(ValueError):
        decode_value(manifest, (chunk,))


def test_full_sixteen_mib_canonical_object_round_trip_and_limit():
    source = random.Random(104)
    value = source.randbytes((MAX_OBJECT_BYTES - 2) // 2).hex()
    assert len(canonical_json(value)) == MAX_OBJECT_BYTES
    manifest, chunks = encode_value(value)
    assert manifest.raw_size == MAX_OBJECT_BYTES
    assert len(chunks) > 200
    assert decode_value(manifest, chunks) == value
    with pytest.raises(ValueError):
        encode_value(value + 'x')


def test_binary_control_frame_fits_sixty_four_kib_and_checks_digest():
    binary = D1Binary(bytes(range(256)) * 128)
    assert len(json.dumps(binary.wire).encode()) < 65536
    assert D1Binary.from_wire(binary.wire) == binary
    forged = dict(binary.wire, sha256='0' * 64)
    with pytest.raises(ValueError):
        D1Binary.from_wire(forged)


@pytest.mark.parametrize('data', [b'', b'x' * 32769, bytearray(b'x'), 'x', None], ids=['empty', 'oversized', 'mutable', 'text', 'none'])
def test_binary_rejects_wrong_type_and_out_of_bounds(data):
    with pytest.raises(ValueError):
        D1Binary(data)


@pytest.mark.parametrize('change', [{'extra': 1}, {'base64': 'eA==\n'}, {'base64': 'eB=='}, {'type': 'blob'}, {'base64': '!' * 50000}])
def test_binary_wire_rejects_ambiguous_or_noncanonical_encoding(change):
    value = D1Binary(b'x').wire
    value.update(change)
    with pytest.raises(ValueError):
        D1Binary.from_wire(value)
