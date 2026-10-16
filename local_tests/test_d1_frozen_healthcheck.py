import sys

import pytest

from adapters.local_d1_objects import LocalD1Objects
from adapters.local_d1_versioned_session import LocalD1VersionedSession
from application.frozen_healthcheck import capture_frozen_healthcheck
from coordination.d1_checkpoints import SCHEMA_STATEMENTS as CHECKPOINTS
from coordination.d1_compute_queue import SCHEMA_STATEMENTS as COMPUTE
from coordination.d1_migrations import AdditiveMigrationPlan
from coordination.d1_objects import SCHEMA_STATEMENTS as OBJECTS
from coordination.d1_recovery import SCHEMA_STATEMENTS as RECOVERY
from coordination.d1_schema import D1Schema
from coordination.d1_statement import D1Statement
from coordination.receipt_d1_contract import SCHEMA_STATEMENTS as HEADS
from domain.chunks import encode_document, encode_value
from domain.snapshots import canonical_json, restore_document
from executors.local.frozen_healthcheck import run_frozen_healthcheck
from local_tests.test_healthcheck_artifact import frozen_fixture


@pytest.mark.skipif(sys.platform != 'win32', reason='requires verified Windows SDK and native child supervision')
def test_sealed_d1_documents_compute_in_pure_child_and_seal_same_result_without_claiming_a_job(tmp_path):
    config, training, inputs, scope, code, expected = frozen_fixture(tmp_path)
    initial = D1Schema((*OBJECTS, *HEADS, *COMPUTE, *CHECKPOINTS, *RECOVERY))
    plan = AdditiveMigrationPlan((initial, D1Schema((*initial.statements, 'CREATE TABLE mock_future(id TEXT)'))))
    directory = tmp_path / 'd1'
    session = LocalD1VersionedSession(directory, plan, 0, 1, is_create=True)
    objects = LocalD1Objects.with_session(session)
    for document in (config, training, inputs):
        manifest, chunks = encode_document(document)
        assert manifest.object_id == document.snapshot_id
        objects.begin(manifest)
        objects.put_many(manifest, chunks)
        objects.seal(manifest)
    reopened = LocalD1Objects.with_session(LocalD1VersionedSession(directory, plan, 0, 1))
    loaded = tuple(restore_document(canonical_json(reopened.read(document.snapshot_id)), document.snapshot_id)
                   for document in (config, training, inputs))
    execution = run_frozen_healthcheck(capture_frozen_healthcheck(*loaded, scope, code))
    assert execution.state == 'computed' and execution.artifact == expected
    manifest, chunks = encode_value(execution.artifact)
    assert manifest.object_id == execution.result_id
    reopened.begin(manifest)
    reopened.put_many(manifest, chunks)
    assert reopened.seal(manifest) == expected
    assert reopened.read(manifest.object_id) == expected
    # This proves transport/calculation parity, not AUTO adoption or job completion.
    counts = session.execute((D1Statement('SELECT COUNT(*) AS count FROM compute_jobs', ()),
                              D1Statement('SELECT COUNT(*) AS count FROM object_heads', ())))
    assert counts[0]['results'] == [{'count': 0}]
    assert counts[1]['results'] == [{'count': 4}]
