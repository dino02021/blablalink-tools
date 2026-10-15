"""Member-scoped supplementary observations; never publish or silently relabel old raw."""

from dataclasses import asdict

from application.collection import collect_training
from application.collection_checkpoint import CollectionCheckpoint
from application.compute_budget import ComputeExecutionCheck, ComputeLimit
from application.reference_catalog import read_reference
from application.tasks import StateConflict, TaskSpec
from application.training_reuse import plan_training_reuse
from domain.execution import check_execution, execution_checks
from domain.member_epochs import validate_member_epochs
from domain.normalization import capture_raw_training, normalize_training
from domain.snapshots import MemberKey, Override, canonical_json, capture_document


def prepare_supplementary_inputs(config, raw, reference, union_inputs, code_sha, captured_at):
    if (len(raw.serialized.encode()) > 16*1024*1024 or len(config.serialized.encode()) > 8*1024*1024
            or len(union_inputs.serialized.encode()) > 8*1024*1024):
        raise ValueError('supplementary source exceeds byte bound')
    catalog, prefixes = read_reference(reference)
    if (union_inputs.envelope['kind'] != 'union_inputs' or union_inputs.envelope['scope_id'] != raw.envelope['scope_id']
            or union_inputs.envelope['reference_version'] != reference.envelope['reference_version']):
        raise StateConflict('supplementary input scope or reference differs')
    roster, names, overrides = _cohort(union_inputs)
    plan = plan_training_reuse(config, raw, catalog.reference, prefixes, roster, names, code_sha, overrides, union_inputs.payload.get('member_epochs'))
    if plan.blockers or plan.is_ready or not plan.requests:
        raise StateConflict('supplementary collection requires an actionable fixed gap plan')
    required = {request.member for request in plan.requests}
    members = tuple(member for member in roster if member in required)
    epochs = validate_member_epochs(union_inputs.payload.get('member_epochs'), roster)
    return capture_document('union_inputs', raw.envelope['scope_id'], captured_at, reference.envelope['reference_version'],
        {'collection_kind': 'supplementary_training_inputs', 'supplementary_schema_version': 1,
            'config_id': config.snapshot_id, 'base_raw_id': raw.snapshot_id, 'reference_id': reference.snapshot_id,
            'union_inputs_id': union_inputs.snapshot_id, 'code_sha': code_sha, 'plan_id': plan.plan_id,
            'plan': asdict(plan), 'members': [asdict(member) for member in members], 'role_ids': catalog.reference.role_ids,
            'function_prefixes': prefixes, 'member_epochs': {member.key: epochs[member.key] for member in members if member.key in epochs}})


def supplementary_task_spec(inputs, intent_id, mock_script_id=None):
    data = inputs.payload
    if data.get('collection_kind') != 'supplementary_training_inputs':
        raise ValueError('supplementary inputs required')
    return TaskSpec('refresh_roster_and_training', inputs.envelope['scope_id'], data['code_sha'],
        {'collection_inputs': inputs.snapshot_id, 'union_inputs': data['union_inputs_id'],
            'healthcheck_config': data['config_id'], 'base_raw': data['base_raw_id'], 'reference': data['reference_id'],
            **({'mock_collection_script': mock_script_id} if mock_script_id is not None else {})},
        family='collection', intent_id=intent_id)


def merge_supplementary_raw(base, fresh, union_inputs, expected_members):
    expected_members = tuple(expected_members)
    roster, _, _ = _cohort(union_inputs)
    if (base.envelope['kind'] != 'raw' or fresh.envelope['kind'] != 'raw' or union_inputs.envelope['kind'] != 'union_inputs'
            or base.envelope['scope_id'] != fresh.envelope['scope_id'] or fresh.envelope['scope_id'] != union_inputs.envelope['scope_id']
            or len({document.envelope['reference_version'] for document in (base, fresh, union_inputs)}) != 1
            or not isinstance(base.payload.get('reference_role_ids'), list)
            or base.payload['reference_role_ids'] != fresh.payload.get('reference_role_ids')
            or len(expected_members) != len(set(expected_members)) or not expected_members or set(expected_members)-set(roster)):
        raise StateConflict('supplementary raw source dependencies differ')
    # Re-validate source fields and identities through the canonical whitelist.
    for source in (base, fresh):
        capture_raw_training(source.envelope['scope_id'], source.envelope['captured_at'], source.envelope['reference_version'],
            tuple(source.payload['members']), tuple(source.payload['reference_role_ids']), source.payload.get('member_epochs'))
    base_rows = {MemberKey(row['area_id'], row['member_id']): row for row in base.payload['members']}
    fresh_rows = {MemberKey(row['area_id'], row['member_id']): row for row in fresh.payload['members']}
    if set(fresh_rows) != set(expected_members):
        raise StateConflict('fresh observation must cover exactly the requested members')
    required_epochs = validate_member_epochs(union_inputs.payload.get('member_epochs'), roster)
    fresh_epochs = validate_member_epochs(fresh.payload.get('member_epochs'), fresh_rows)
    if any(fresh_epochs.get(member.key) != required_epochs[member.key] for member in expected_members if member.key in required_epochs):
        raise StateConflict('fresh observation membership epoch differs')
    epochs, rows, lineage = {}, [], []
    for member in roster:
        check_execution()
        source = fresh if member in fresh_rows else base
        row = fresh_rows.get(member) if member in fresh_rows else base_rows.get(member)
        if row is None:
            continue  # still unknown; the subsequent reuse plan requests this member.
        rows.append(row)
        if member.key in source.payload.get('member_epochs', {}):
            epochs[member.key] = source.payload['member_epochs'][member.key]
        lineage.append({'member': asdict(member), 'source_raw_id': source.snapshot_id,
            'source_document_captured_at': source.envelope['captured_at']})
    assembled = capture_raw_training(fresh.envelope['scope_id'], fresh.envelope['captured_at'], fresh.envelope['reference_version'],
        tuple(rows), tuple(base.payload['reference_role_ids']), epochs)
    merged = capture_document('raw', assembled.envelope['scope_id'], assembled.envelope['captured_at'], assembled.envelope['reference_version'],
        {**assembled.payload, 'supplementary_lineage': {'schema_version': 1, 'base_raw_id': base.snapshot_id,
            'fresh_raw_id': fresh.snapshot_id, 'union_inputs_id': union_inputs.snapshot_id, 'members': lineage}})
    if len(merged.serialized.encode()) > 16*1024*1024:
        raise ComputeLimit('supplementary_raw_capacity')
    return merged


def run_supplementary_collection_job(store, lease, port, captured_at, budget):
    try:
        with execution_checks(ComputeExecutionCheck(store, lease, budget)):
            check_execution()
            return _run_supplementary_collection_job(store, lease, port, captured_at)
    except ComputeLimit as error:
        return store.defer(lease, 'capacity_blocked', error.code)


def _run_supplementary_collection_job(store, lease, port, captured_at):
    stale = store.supersede_if_stale(lease)
    if stale is not None:
        return stale
    job = store.get_job(lease.job_id)
    inputs = store.get_document(job.spec.version_map['collection_inputs'])
    data = inputs.payload
    config, base, reference, union_inputs = (store.get_document(data[key]) for key in ('config_id','base_raw_id','reference_id','union_inputs_id'))
    rebuilt = prepare_supplementary_inputs(config, base, reference, union_inputs, job.spec.code_sha, inputs.envelope['captured_at'])
    if rebuilt != inputs or supplementary_task_spec(inputs, job.spec.intent_id, job.spec.version_map.get('mock_collection_script')) != job.spec:
        raise StateConflict('supplementary task differs from fixed inputs')
    members = tuple(MemberKey(**row) for row in data['members'])
    checkpoint = CollectionCheckpoint(store, lease, inputs, job.spec.code_sha)
    fresh = collect_training(port, job.spec.scope_id, captured_at, inputs.envelope['reference_version'], members,
        tuple(data['role_ids']), checkpoint=checkpoint, member_epochs=data['member_epochs'])
    normalized_fresh = normalize_training(fresh, tuple(data['role_ids']), data['function_prefixes'])
    store.put_document(fresh)
    store.put_document(normalized_fresh.training)
    checkpoint.record_observation(fresh, normalized_fresh.training)
    if normalized_fresh.is_auth_required:
        return store.defer(lease, 'auth_required', 'collection_auth_required')
    statuses = [source['status'] for member in fresh.payload['collection_proof'] for source in member['sources'].values()]
    if any(status != 'complete' for status in statuses):
        return store.defer(lease, 'retry_wait', 'supplementary_collection_incomplete', delay_seconds=5)
    merged = merge_supplementary_raw(base, fresh, union_inputs, members)
    normalized = normalize_training(merged, tuple(data['role_ids']), data['function_prefixes'])
    roster, names, overrides = _cohort(union_inputs)
    catalog, prefixes = read_reference(reference)
    next_plan = plan_training_reuse(config, merged, catalog.reference, prefixes, roster, names, job.spec.code_sha,
        overrides, union_inputs.payload.get('member_epochs'))
    raw_id, training_id = store.put_document(merged), store.put_document(normalized.training)
    check_execution()
    return store.complete_compute(lease, {'raw_snapshot_id': raw_id, 'training_snapshot_id': training_id,
        'base_raw_id': base.snapshot_id, 'fresh_raw_id': fresh.snapshot_id, 'fresh_training_id': normalized_fresh.training.snapshot_id,
        'collection_inputs_id': inputs.snapshot_id, 'collection_checkpoint_id': checkpoint.latest_id,
        'is_training_ready': next_plan.is_ready, 'followup_plan': asdict(next_plan), 'followup_plan_id': next_plan.plan_id}, is_publication_required=False)


def _cohort(inputs):
    rows = inputs.payload['members']
    if not isinstance(rows, list) or not 1 <= len(rows) <= 300:
        raise ValueError('bounded nonempty supplementary cohort required')
    roster = tuple(MemberKey(**row['member']) for row in rows)
    names = {MemberKey(**row['member']).key: row['display_name'] for row in rows}
    overrides = tuple(Override(**{**row, 'member': MemberKey(**row['member'])}) for row in inputs.payload.get('overrides', []))
    return roster, names, overrides
