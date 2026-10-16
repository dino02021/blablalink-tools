import json
import sys

import pytest

from adapters.local_d1_objects import LocalD1Objects
from adapters.local_d1_versioned_session import LocalD1VersionedSession
from coordination.d1_migrations import AdditiveMigrationPlan, begin_migration
from coordination.d1_schema import D1Schema
from coordination.d1_objects import SCHEMA_STATEMENTS as OBJECTS
from coordination.d1_statement import D1Statement
from domain.chunks import encode_value
from local_tests.test_d1_migrations import definition


@pytest.mark.parametrize('revision,epoch', [(True, 1), (-1, 1), (3, 1), (0, True), (0, 0), (1, 2)])
def test_invalid_initial_pin_rejected_before_creating_workspace(tmp_path, revision, epoch):
    with pytest.raises(ValueError):
        LocalD1VersionedSession(tmp_path / 'd1', definition(), revision, epoch, is_create=True)
    assert not (tmp_path / 'd1').exists()


def test_frame_preserves_bounds_and_validates_both_guard_results():
    session = object.__new__(LocalD1VersionedSession)
    plan = definition()
    from coordination.d1_migrations import LEDGER, ready_guard
    session.schema = D1Schema((*plan.schemas[0].statements, *LEDGER.statements))
    session._readiness = ready_guard(plan, 0, 1)
    frame = session._frame((D1Statement('SELECT 1', ()),) * 48)
    assert len(frame['statements']) == 50
    with pytest.raises(ValueError):
        session._frame((D1Statement('SELECT 1', ()),) * 49)
    result = {'outcome': 'completed', 'results': [
        {'success': True, 'results': [{'schema_ok': 1}]},
        {'success': True, 'results': [{'schema_ready': 1}]},
        {'success': True, 'results': [{'value': 2}]}]}
    assert session._verify(result, 1) == result['results'][2:]
    result['results'][1]['results'] = [{'schema_ready': 0}]
    with pytest.raises(ValueError):
        session._verify(result, 1)
    result['results'][1]['results'] = [{'schema_ready': True}]
    with pytest.raises(ValueError):
        session._verify(result, 1)
    for invalid in (None, {}, {'outcome': 'completed', 'results': [None] * 3}):
        with pytest.raises(ValueError):
            session._verify(invalid, 1)


@pytest.mark.skipif(sys.platform != 'win32', reason='requires verified Windows SDK supervision')
def test_actual_versioned_session_reopen_is_read_only_and_old_epoch_cannot_write(tmp_path, monkeypatch):
    from adapters import local_d1_session
    plan = definition()
    directory = tmp_path / 'd1'
    session = LocalD1VersionedSession(directory, plan, 0, 1, is_create=True)
    session.execute((D1Statement("INSERT INTO mock_existing VALUES('mock-id','preserved')", ()),))
    captured = []
    original = local_d1_session.run_d1_emulator
    def invoke(*args, **kwargs):
        receipt = original(*args, **kwargs)
        captured.append(json.loads(receipt['stdout']))
        return receipt
    monkeypatch.setattr(local_d1_session, 'run_d1_emulator', invoke)
    restarted = LocalD1VersionedSession(directory, plan, 0, 1)
    assert sum(result['meta']['rows_written'] for operation in captured[0]['operations'] for result in operation['results']) == 0
    restarted.execute(begin_migration(plan, 0, 1))
    for current in (session, restarted):
        with pytest.raises(ValueError):
            current.execute((D1Statement("INSERT INTO mock_existing VALUES('stale','bad')", ()),))
    with pytest.raises(ValueError):
        LocalD1VersionedSession(directory, plan, 0, 1)
    with pytest.raises(ValueError):
        LocalD1VersionedSession(directory, plan, 0, 2)


@pytest.mark.skipif(sys.platform != 'win32', reason='requires verified Windows SDK supervision')
def test_actual_versioned_object_frames_share_schema_and_readiness_guards(tmp_path):
    initial = D1Schema(OBJECTS)
    after = D1Schema((*OBJECTS, 'CREATE TABLE mock_future(id TEXT)'))
    plan = AdditiveMigrationPlan((initial, after))
    session = LocalD1VersionedSession(tmp_path / 'd1', plan, 0, 1, is_create=True)
    objects = LocalD1Objects.with_session(session)
    value = {'mock': 'frozen data', 'zero': 0}
    manifest, chunks = encode_value(value)
    objects.begin(manifest)
    objects.put_many(manifest, chunks)
    assert objects.seal(manifest) == value
    frames = session.execute_frames(((D1Statement('SELECT COUNT(*) AS count FROM object_heads', ()),),
                                     (D1Statement('SELECT revision,epoch FROM coordinator_schema_authority', ()),)))
    assert frames[0][0]['results'] == [{'count': 1}]
    assert frames[1][0]['results'] == [{'revision': 0, 'epoch': 1}]


@pytest.mark.skipif(sys.platform != 'win32', reason='requires verified Windows SDK supervision')
def test_actual_versioned_reopen_does_not_recreate_missing_business_table(tmp_path):
    from executors.local.emulator_process import run_d1_emulator
    plan = definition()
    directory = tmp_path / 'd1'
    session = LocalD1VersionedSession(directory, plan, 0, 1, is_create=True)
    session.execute((D1Statement('DROP TABLE mock_existing', ()),))
    with pytest.raises(ValueError):
        LocalD1VersionedSession(directory, plan, 0, 1)
    receipt = run_d1_emulator({'schema_statements': [], 'operations': [{'kind': 'query',
        'sql': "SELECT COUNT(*) AS count FROM sqlite_master WHERE name='mock_existing'", 'parameters': []}]},
        mock_directory=directory, schema_id=session.schema.schema_id)
    result = json.loads(receipt['stdout'])
    assert result.get('outcome') != 'emulator_failed', result
    assert result['operations'][0]['result']['results'] == [{'count': 0}]
