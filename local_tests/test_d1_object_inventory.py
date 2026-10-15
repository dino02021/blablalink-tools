import hashlib
import json
import sys

import pytest

from adapters.local_d1_objects import LocalD1Objects
from coordination.d1_objects import SCHEMA_ID, list_chunks, transport_id
from domain.chunks import ChunkManifest
from domain.snapshots import canonical_json
from executors.local.emulator_process import run_d1_emulator


def maximum_manifest():
    chunk_hash = hashlib.sha256(bytes(32768)).hexdigest()
    return ChunkManifest('a' * 64, 'a' * 64, 16 * 1024 * 1024, 16 * 1024 * 1024, (chunk_hash,) * 512)


class InventoryStore(LocalD1Objects):
    def __init__(self, manifest, missing=(), corrupt=None):
        self.manifest = manifest
        self.omitted = missing
        self.corrupt = corrupt
        self.pages = []

    def _execute(self, statements, **kwargs):
        after = statements[-1].parameters[1]
        rows = [{'ordinal': index, 'chunk_hash': self.manifest.chunk_hashes[index], 'size': 32768}
                for index in range(after + 1, 512) if index not in self.omitted][:64]
        if self.corrupt and after == -1:
            rows = self.corrupt(rows)
        self.pages.append(rows)
        if len(statements) == 1:
            return [rows]
        return [[{'transport_id': transport_id(self.manifest), 'object_id': self.manifest.object_id,
                  'manifest': self.manifest.serialized, 'is_sealed': 0}], rows]


@pytest.mark.parametrize('omitted', [(), (0, 64, 511), (63, 127, 255, 510)])
def test_maximum_inventory_pages_and_gaps_are_bounded(omitted):
    manifest = maximum_manifest()
    store = InventoryStore(manifest, omitted)
    assert store.missing(manifest) == omitted
    assert len(store.pages) == 8 and all(len(page) <= 64 for page in store.pages)
    first = {'manifest': manifest.serialized, 'page': store.pages[0]}
    assert len(canonical_json(first).encode()) < 65536


@pytest.mark.parametrize('change', ['duplicate', 'reordered', 'oversized', 'wrong_hash', 'wrong_size', 'unknown_field'])
def test_inventory_does_not_trust_malformed_page(change):
    def corrupt(rows):
        if change == 'duplicate': rows[1] = dict(rows[0])
        elif change == 'reordered': rows[0], rows[1] = rows[1], rows[0]
        elif change == 'oversized': rows.append(dict(rows[-1]))
        elif change == 'wrong_hash': rows[0]['chunk_hash'] = '0' * 64
        elif change == 'wrong_size': rows[0]['size'] = True
        else: rows[0]['extra'] = 1
        return rows
    manifest = maximum_manifest()
    with pytest.raises(ValueError):
        InventoryStore(manifest, corrupt=corrupt).missing(manifest)


@pytest.mark.parametrize('cursor', [True, -2, 512, '0', None])
def test_inventory_cursor_requires_bounded_integer(cursor):
    with pytest.raises(ValueError):
        list_chunks('a' * 64, cursor)


@pytest.mark.skipif(sys.platform != 'win32', reason='requires verified Windows SDK process supervision')
def test_actual_d1_largest_declared_inventory_does_not_exceed_output(tmp_path):
    manifest = maximum_manifest()
    store = LocalD1Objects(tmp_path / 'd1', is_create=True)
    store.begin(manifest)
    # Deliberately not valid zlib: this fixture proves inventory bounds only.
    receipt = run_d1_emulator({'schema_statements': [], 'operations': [{
        'kind': 'query', 'sql': 'WITH RECURSIVE ids(n) AS (SELECT 0 UNION ALL SELECT n+1 FROM ids WHERE n<511) '
        'INSERT INTO object_chunks SELECT ?,n,?,zeroblob(32768) FROM ids',
        'parameters': [transport_id(manifest), manifest.chunk_hashes[0]],
    }]}, mock_directory=store.directory, schema_id=SCHEMA_ID)
    assert receipt['outcome'] == 'completed'
    assert json.loads(receipt['stdout'])['operations'][0]['outcome'] == 'completed'
    assert store.missing(manifest) == ()
    with pytest.raises(ValueError):
        store.read(manifest.object_id)
