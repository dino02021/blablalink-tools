import json
import sys

import pytest

from coordination.d1_binary import D1Binary
from executors.local.emulator_process import run_d1_emulator


@pytest.mark.skipif(sys.platform != 'win32', reason='requires verified Windows SDK process supervision')
@pytest.mark.parametrize('is_corrupt', [False, True])
def test_actual_d1_binary_binding_and_integrity_rejection(is_corrupt):
    wire = D1Binary(bytes(range(256)) * 128).wire
    if is_corrupt:
        wire['sha256'] = '0' * 64
    receipt = run_d1_emulator({'schema_statements': ['CREATE TABLE chunks(data BLOB NOT NULL)'], 'operations': [
        {'kind': 'query', 'sql': 'INSERT INTO chunks VALUES(?)', 'parameters': [wire]},
        {'kind': 'query', 'sql': 'SELECT COUNT(*) AS count,MAX(length(data)) AS size,MAX(typeof(data)) AS kind FROM chunks', 'parameters': []},
    ]})
    assert receipt['outcome'] == 'completed' and receipt['is_child_stopped']
    result = json.loads(receipt['stdout'])
    assert result['are_sdk_children_closed']
    assert result['operations'][0]['outcome'] == ('rejected' if is_corrupt else 'completed')
    assert result['operations'][1]['result']['results'] == [
        {'count': 0, 'size': None, 'kind': None} if is_corrupt else {'count': 1, 'size': 32768, 'kind': 'blob'}]


@pytest.mark.skipif(sys.platform != 'win32', reason='requires verified Windows SDK process supervision')
def test_actual_maximum_chunk_round_trip_stays_within_output_bound():
    data = bytes(range(256)) * 128
    receipt = run_d1_emulator({'schema_statements': ['CREATE TABLE chunks(chunk_data BLOB NOT NULL)'], 'operations': [
        {'kind': 'query', 'sql': 'INSERT INTO chunks VALUES(?)', 'parameters': [D1Binary(data).wire]},
        {'kind': 'query', 'sql': 'SELECT chunk_data FROM chunks', 'parameters': []},
    ]})
    assert receipt['outcome'] == 'completed' and len(receipt['stdout'].encode()) < 65536
    result = json.loads(receipt['stdout'])
    assert D1Binary.from_wire(result['operations'][1]['result']['results'][0]['chunk_data']).data == data
