import pytest

from adapters.local_state import LocalStateStore
from application.healthcheck_artifact import HealthcheckInputsPending, build_healthcheck_artifact
from application.tasks import StateConflict
from domain.snapshots import MemberKey, canonical_json, capture_document
from executors.local.demo import run_demo


def frozen_fixture(tmp_path):
    directory = tmp_path / 'root-mock'
    summary = run_demo(directory)
    store = LocalStateStore(directory / 'state.sqlite')
    try:
        job = store.get_job(summary['job_id'])
        versions = job.spec.version_map
        documents = tuple(store.get_document(versions[name]) for name in ('healthcheck_config', 'training', 'union_inputs'))
        return (*documents, job.spec.scope_id, job.spec.code_sha, store.get_result(job.result_id))
    finally:
        store.close()


def with_payload(document, payload, *, scope_id=None):
    envelope = document.envelope
    return capture_document(envelope['kind'], scope_id or envelope['scope_id'], envelope['captured_at'],
                            envelope['reference_version'], payload)


def test_pure_artifact_matches_existing_root_result_and_retains_exact_standards(tmp_path):
    config, training, inputs, scope, code, expected = frozen_fixture(tmp_path)
    artifact = build_healthcheck_artifact(config, training, inputs, scope, code)
    assert canonical_json(artifact) == canonical_json(expected)
    generated = next(iter(artifact['workbooks'].values()))
    assert generated['standards'] == config.payload
    assert generated['config_id'] == config.snapshot_id
    assert generated['snapshot_id'] == training.snapshot_id


@pytest.mark.parametrize('reason', ['training_incomplete', 'training_membership_epoch_unproven'])
def test_pure_missing_data_has_same_explicit_pending_reason(tmp_path, reason):
    config, training, inputs, scope, code, _ = frozen_fixture(tmp_path)
    if reason == 'training_incomplete':
        training = with_payload(training, {**training.payload, 'records': []})
    else:
        epochs = {MemberKey(**row['member']).key: 'a' * 64 for row in inputs.payload['members']}
        inputs = with_payload(inputs, {**inputs.payload, 'member_epochs': epochs})
    with pytest.raises(HealthcheckInputsPending) as pending:
        build_healthcheck_artifact(config, training, inputs, scope, code)
    assert pending.value.code == reason


def test_pure_scope_mismatch_does_not_calculate_or_publish_another_union(tmp_path):
    config, training, inputs, scope, code, _ = frozen_fixture(tmp_path)
    inputs = with_payload(inputs, inputs.payload, scope_id='mock_other_union')
    with pytest.raises(StateConflict, match='scope'):
        build_healthcheck_artifact(config, training, inputs, scope, code)
