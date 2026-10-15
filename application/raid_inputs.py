"""Compile raid inputs from sealed, published healthcheck and explicit human inputs."""

from collections import Counter
from dataclasses import asdict

from application.healthcheck import calculate_healthcheck
from application.tasks import StateConflict, TaskSpec, validate_identifier
from domain.healthcheck import ELEMENTS
from domain.member_epochs import validate_member_epochs
from domain.raid import FixedInputs, RaidKnife, RaidMember
from domain.raid_planner import RaidProblem
from domain.snapshots import MemberKey, Override, canonical_json, read_training

COMPILER = 'raid-input-v1'


def raid_task_spec(scope_id, problem):
    versions = dict(problem.input_versions)
    if versions.get('raid_compiler') != COMPILER:
        raise ValueError('compiled raid problem required')
    return TaskSpec('generate_raid_draft', scope_id, problem.code_sha, {**versions, 'problem': problem.problem_id}, family='raid')


def compile_raid_problem(store, healthcheck_job_id, settings_id, member_inputs_id, code_sha):
    validate_identifier(code_sha)
    job = store.get_job(healthcheck_job_id)
    if (job.spec.command != 'recompute_healthcheck' or job.spec.code_sha != code_sha
            or not store.has_successful_publication(job.job_id) or not store.is_job_current(job.job_id)):
        raise StateConflict('raid ranks require current published healthcheck of the same code')
    versions = job.spec.version_map
    inputs = store.get_document(versions['union_inputs'])
    config = store.get_document(versions['healthcheck_config'])
    training = store.get_document(versions['training'])
    scope, reference = inputs.envelope['scope_id'], inputs.envelope['reference_version']
    if (scope != job.spec.scope_id or training.envelope['scope_id'] != scope or training.envelope['reference_version'] != reference
            or inputs.envelope['kind'] != 'union_inputs'):
        raise StateConflict('raid healthcheck input scope or reference differs')
    rows = inputs.payload['members']
    if not rows or len(rows) > 300:
        raise ValueError('bounded nonempty raid roster required')
    roster = tuple(MemberKey(**row['member']) for row in rows)
    names = {MemberKey(**row['member']).key: row['display_name'] for row in rows}
    required_epochs = validate_member_epochs(inputs.payload.get('member_epochs'), roster)
    epochs = validate_member_epochs(training.payload.get('member_epochs'), tuple(record.member for record in read_training(training)))
    if any(epochs.get(key) != epoch for key, epoch in required_epochs.items()):
        raise StateConflict('raid healthcheck membership epoch is unproven')
    overrides = tuple(Override(**{**row, 'member': MemberKey(**row['member'])}) for row in inputs.payload.get('overrides', []))
    calculated = calculate_healthcheck(config, training, roster, names, code_sha, overrides)
    result = store.get_result(job.result_id)
    books = result.get('workbooks', {})
    if len(books) != 1:
        raise StateConflict('one sealed healthcheck workbook required')
    generated = next(iter(books.values()))
    if (generated['config_id'] != config.snapshot_id or generated['snapshot_id'] != training.snapshot_id
            or generated['code_sha'] != code_sha or generated['calculation_id'] != calculated.result_id
            or canonical_json(generated['scores']) != canonical_json(asdict(calculated.scores))):
        raise StateConflict('raid ranks differ from sealed source calculation')
    settings = _source(store, settings_id, scope, reference, 'raid_settings')
    member_inputs = _source(store, member_inputs_id, scope, reference, 'raid_member_inputs')
    settings_fields = {'input_kind', 'raid_schema_version', 'hp', 'boss_elements', 'base_sync', 'base_damage_yi', 'element_tie_order', 'overflow_policy'}
    if set(settings) != settings_fields or set(member_inputs) != {'input_kind', 'raid_schema_version', 'members', 'history'}:
        raise ValueError('explicit raid input schema required')
    indexed = {}
    if not isinstance(member_inputs['members'], list) or len(member_inputs['members']) > 300:
        raise ValueError('bounded explicit raid member inputs required')
    for row in member_inputs['members']:
        if not isinstance(row, dict) or set(row) != {'member', 'sync', 'fixed_inputs'}:
            raise ValueError('raid member fields differ')
        member = MemberKey(**row['member'])
        if member in indexed or member not in roster:
            raise ValueError('duplicate or foreign raid member')
        if set(row['fixed_inputs']) != {'slots', 'damage_overrides_yi'}:
            raise ValueError('explicit fixed slots and overrides required')
        indexed[member] = (RaidMember(member, names[member.key], row['sync']), FixedInputs(**row['fixed_inputs']))
    if set(indexed) != set(roster):
        raise ValueError('raid inputs must cover every current member')
    if not isinstance(member_inputs['history'], list) or len(member_inputs['history']) > 900:
        raise ValueError('bounded raid history required')
    history = tuple(RaidKnife(**{**row, 'member': MemberKey(**row['member'])}) for row in member_inputs['history'])
    if (any(knife.kind != 'history' or knife.member not in indexed for knife in history)
            or len({knife.knife_id for knife in history}) != len(history)
            or any(count > 3 for count in Counter(knife.member for knife in history).values())):
        raise ValueError('raid history identity or count differs')
    rank_map = {(score.member_id, score.key): score.rank for score in calculated.scores.groups}
    dependencies = {'raid_compiler': COMPILER, 'healthcheck_job': job.job_id, 'healthcheck_result': job.result_id,
        'healthcheck_config': config.snapshot_id, 'training': training.snapshot_id, 'union_inputs': inputs.snapshot_id,
        'raid_settings': settings_id, 'raid_member_inputs': member_inputs_id}
    return RaidProblem(tuple(indexed[member][0] for member in roster), settings['hp'], settings['boss_elements'], settings['base_sync'],
        settings['base_damage_yi'], history, tuple((member, indexed[member][1]) for member in roster),
        tuple((member, tuple((element, rank_map.get((member.key, element))) for element in ELEMENTS)) for member in roster),
        tuple(dependencies.items()), code_sha, tuple(settings['element_tie_order']), settings['overflow_policy'])


def is_raid_dependency_current(store, scope_id, problem):
    versions = dict(problem.input_versions)
    managed = store.union_input_version(scope_id)
    if managed is not None and (versions.get('union_inputs') != managed['input_id'] or managed['roster_state'] != 'active' or versions.get('raid_compiler') != COMPILER):
        return False
    if 'raid_compiler' not in versions:
        return True
    if versions['raid_compiler'] != COMPILER:
        return False
    job = store.get_job(versions['healthcheck_job'])
    return (job.spec.command == 'recompute_healthcheck' and job.spec.scope_id == scope_id and job.spec.code_sha == problem.code_sha and job.result_id == versions['healthcheck_result']
        and all(job.spec.version_map.get(key) == versions[key] for key in ('union_inputs', 'healthcheck_config', 'training'))
        and store.has_successful_publication(job.job_id) and store.is_job_current(job.job_id))


def _source(store, source_id, scope, reference, kind):
    document = store.get_document(source_id)
    if (document.envelope['kind'] != 'union_inputs' or document.envelope['scope_id'] != scope
            or document.envelope['reference_version'] != reference or len(document.serialized.encode()) > 8*1024*1024
            or document.payload.get('input_kind') != kind or type(document.payload.get('raid_schema_version')) is not int
            or document.payload['raid_schema_version'] != 1):
        raise StateConflict('raid source kind, scope, reference or schema differs')
    return document.payload
