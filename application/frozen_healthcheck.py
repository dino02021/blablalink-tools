"""Canonical offline input bundle and snapshot-bound result validation."""

from dataclasses import dataclass, field
import hashlib
import json

from application.tasks import validate_identifier
from domain.chunks import MAX_OBJECT_BYTES
from domain.snapshots import Document, canonical_json, restore_document
from domain.view_grid import restore_workbook_view

DOCUMENT_NAMES = ('healthcheck_config', 'training', 'union_inputs')
MAX_BUNDLE_BYTES = 3 * MAX_OBJECT_BYTES + 4096


@dataclass(frozen=True)
class FrozenHealthcheckBundle:
    serialized: str = field(repr=False)
    scope_id: str = field(init=False)
    code_sha: str = field(init=False)
    bundle_id: str = field(init=False)
    source_ids: tuple[str, ...] = field(init=False)

    def __post_init__(self):
        if type(self.serialized) is not str or not 0 < len(self.serialized.encode('utf-8')) <= MAX_BUNDLE_BYTES:
            raise ValueError('bounded canonical frozen healthcheck bundle required')
        value = json.loads(self.serialized)
        if (type(value) is not dict or set(value) != {'schema_version', 'is_mock', 'kind', 'scope_id', 'code_sha', 'documents'}
                or type(value['schema_version']) is not int or value['schema_version'] != 1
                or value['is_mock'] is not True or value['kind'] != 'frozen_healthcheck_v1'
                or canonical_json(value) != self.serialized or type(value['documents']) is not dict
                or set(value['documents']) != set(DOCUMENT_NAMES)):
            raise ValueError('frozen healthcheck bundle identity differs')
        validate_identifier(value['scope_id'])
        validate_identifier(value['code_sha'])
        documents = _restore_documents(value)
        config, training, inputs = (document.envelope for document in documents)
        if (training['scope_id'] != value['scope_id'] or inputs['scope_id'] != value['scope_id']
                or config['scope_id'] not in ('common', value['scope_id'])
                or len({document['reference_version'] for document in (config, training, inputs)}) != 1):
            raise ValueError('frozen healthcheck scopes or references differ')
        object.__setattr__(self, 'scope_id', value['scope_id'])
        object.__setattr__(self, 'code_sha', value['code_sha'])
        object.__setattr__(self, 'bundle_id', hashlib.sha256(self.serialized.encode('utf-8')).hexdigest())
        object.__setattr__(self, 'source_ids', tuple(document.snapshot_id for document in documents))

    def documents(self):
        return _restore_documents(json.loads(self.serialized))


def capture_frozen_healthcheck(config, training, inputs, scope_id, code_sha):
    documents = (config, training, inputs)
    if any(not isinstance(document, Document) for document in documents):
        raise ValueError('immutable frozen healthcheck documents required')
    value = {'schema_version': 1, 'is_mock': True, 'kind': 'frozen_healthcheck_v1',
             'scope_id': scope_id, 'code_sha': code_sha, 'documents': {
                 name: {'snapshot_id': document.snapshot_id, 'document': document.envelope}
                 for name, document in zip(DOCUMENT_NAMES, documents)}}
    return FrozenHealthcheckBundle(canonical_json(value))


def validate_frozen_healthcheck_artifact(bundle, artifact):
    if not isinstance(bundle, FrozenHealthcheckBundle) or type(artifact) is not dict or set(artifact) != {'workbooks', 'layout_revisions'}:
        raise ValueError('fixed frozen healthcheck output required')
    config, training, inputs = bundle.documents()
    route = inputs.payload.get('healthcheck_route')
    if type(route) is not dict or set(route) != {'resource', 'layout_revision'} or any(type(value) is not str or not value.strip() for value in route.values()):
        raise ValueError('frozen healthcheck route differs')
    resource = route['resource']
    if (type(artifact['workbooks']) is not dict or set(artifact['workbooks']) != {resource}
            or artifact['layout_revisions'] != {resource: route['layout_revision']}):
        raise ValueError('frozen healthcheck output route differs')
    generated = artifact['workbooks'][resource]
    if (type(generated) is not dict or set(generated) != {'schema_version', 'config_id', 'snapshot_id', 'code_sha',
            'calculation_id', 'standards', 'scores', 'reports', 'view', 'view_id'}
            or type(generated['schema_version']) is not int or generated['schema_version'] != 1
            or generated['config_id'] != config.snapshot_id or generated['snapshot_id'] != training.snapshot_id
            or generated['code_sha'] != bundle.code_sha or canonical_json(generated['standards']) != canonical_json(config.payload)
            or type(generated['scores']) is not dict or type(generated['reports']) is not list
            or type(generated['view']) is not dict):
        raise ValueError('frozen healthcheck output sources differ')
    view = restore_workbook_view(generated['view'])
    if (view.view_id != generated['view_id'] or view.config_id != config.snapshot_id
            or view.training_id != training.snapshot_id or view.code_sha != bundle.code_sha
            or view.calculation_id != generated['calculation_id']):
        raise ValueError('frozen healthcheck view identity differs')


def _restore_documents(value):
    documents = []
    for name in DOCUMENT_NAMES:
        item = value['documents'][name]
        if type(item) is not dict or set(item) != {'snapshot_id', 'document'} or type(item['document']) is not dict:
            raise ValueError('frozen document envelope differs')
        serialized = canonical_json(item['document'])
        if len(serialized.encode('utf-8')) > MAX_OBJECT_BYTES:
            raise ValueError('frozen document exceeds object limit')
        document = restore_document(serialized, item['snapshot_id'])
        if document.serialized != serialized or document.envelope['kind'] != name:
            raise ValueError('frozen document canonical kind differs')
        documents.append(document)
    return tuple(documents)
