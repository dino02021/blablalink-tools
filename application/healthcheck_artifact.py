"""Pure snapshot-bound healthcheck output shared by persistence adapters."""

from dataclasses import asdict

from application.healthcheck import IncompleteTraining, calculate_healthcheck
from application.tasks import StateConflict, validate_identifier
from application.workbook_views import render_healthcheck_view
from domain.execution import check_execution
from domain.member_epochs import validate_member_epochs
from domain.snapshots import Document, MemberKey, Override, read_config, read_training


class HealthcheckInputsPending(RuntimeError):
    def __init__(self, code):
        if code not in ('training_membership_epoch_unproven', 'training_incomplete'):
            raise ValueError('known healthcheck pending reason required')
        super().__init__(code)
        self.code = code


def build_healthcheck_artifact(config, training, inputs, scope_id, code_sha):
    if any(not isinstance(document, Document) for document in (config, training, inputs)):
        raise ValueError('immutable healthcheck documents required')
    validate_identifier(scope_id)
    validate_identifier(code_sha)
    envelope = inputs.envelope
    if (envelope['kind'] != 'union_inputs' or envelope['scope_id'] != scope_id
            or training.envelope['scope_id'] != scope_id
            or envelope['reference_version'] != training.envelope['reference_version']):
        raise StateConflict('healthcheck routing or roster input scope differs')
    roster = tuple(MemberKey(**row['member']) for row in inputs.payload['members'])
    display_names = {MemberKey(**row['member']).key: row['display_name'] for row in inputs.payload['members']}
    overrides = tuple(Override(**{**row, 'member': MemberKey(**row['member'])}) for row in inputs.payload.get('overrides', []))
    required_epochs = validate_member_epochs(inputs.payload.get('member_epochs'), roster)
    training_payload = training.payload
    training_epochs = (validate_member_epochs(training_payload.get('member_epochs'), tuple(record.member for record in read_training(training)))
        if required_epochs or 'member_epochs' in training_payload else {})
    if any(training_epochs.get(member) != epoch for member, epoch in required_epochs.items()):
        raise HealthcheckInputsPending('training_membership_epoch_unproven')
    route = inputs.payload['healthcheck_route']
    if set(route) != {'resource', 'layout_revision'} or any(not isinstance(value, str) or not value.strip() for value in route.values()):
        raise ValueError('fixed workbook route and layout required')
    try:
        result = calculate_healthcheck(config, training, roster, display_names, code_sha, overrides)
    except IncompleteTraining as error:
        raise HealthcheckInputsPending('training_incomplete') from error
    # The exact configuration used in scoring is also retained for publication.
    read_config(config)
    view = render_healthcheck_view(config, training, roster, display_names, result, overrides)
    generated = {'schema_version': 1, 'config_id': result.config_id, 'snapshot_id': result.snapshot_id,
        'code_sha': result.code_version, 'calculation_id': view.calculation_id, 'standards': config.payload,
        'scores': asdict(result.scores), 'reports': [asdict(report) for report in result.reports],
        'view': asdict(view), 'view_id': view.view_id}
    check_execution()
    return {'workbooks': {route['resource']: generated}, 'layout_revisions': {route['resource']: route['layout_revision']}}
