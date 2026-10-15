"""Immutable synthetic collection scripts; no URLs, sessions or real cursor storage."""

from dataclasses import asdict
import hashlib

from adapters.mock_collection import MockCollection
from application.collection import CollectionSourceError
from domain.normalization import capture_raw_training
from domain.snapshots import MemberKey, canonical_json, capture_document

SOURCES = ('ownership','details','effects')


def capture_mock_collection_script(scope_id, captured_at, reference_version, sources):
    if not isinstance(sources, (tuple,list)) or not 1 <= len(sources) <= 900:
        raise ValueError('bounded synthetic source scripts required')
    cleaned, identities = [], set()
    for item in sources:
        if not isinstance(item, dict) or set(item) != {'member','source','outcome','pages'}:
            raise ValueError('explicit mock source schema required')
        member = MemberKey(**item['member'])
        source, outcome, pages = item['source'], item['outcome'], item['pages']
        if (source not in SOURCES or (member,source) in identities or outcome not in ('pages','auth_required','unavailable')
                or not isinstance(pages,list) or len(pages)>100 or (outcome=='pages') != bool(pages)):
            raise ValueError('mock source identity, outcome or pages differ')
        identities.add((member,source))
        if len({identity for identity, _ in identities}) > 300:
            raise ValueError('mock script exceeds member bound')
        clean_pages = []
        for page in pages:
            if (not isinstance(page,dict) or set(page) != {'rows','total','is_options_complete'}
                    or not isinstance(page['rows'],list) or type(page['total']) is not int or not 0<=page['total']<=10000
                    or type(page['is_options_complete']) is not bool or len(canonical_json(page).encode())>1024*1024):
                raise ValueError('bounded mock page required')
            row = {'area_id':member.area_id,'member_id':member.member_id,
                'ownership':{'status':'partial','characters':page['rows'] if source=='ownership' else []},
                'details':{'status':'partial','requested_codes':[], 'character_details':page['rows'] if source=='details' else [],
                    'state_effects':page['rows'] if source=='effects' else []}}
            raw = capture_raw_training(scope_id,captured_at,reference_version,(row,)).payload['members'][0]
            rows = raw['ownership']['characters'] if source=='ownership' else raw['details']['character_details' if source=='details' else 'state_effects']
            if canonical_json(rows) != canonical_json(page['rows']):
                raise ValueError('mock scripts only accept whitelisted source row fields')
            clean_pages.append({**page,'rows':rows})
        if sum(len(page['rows']) for page in clean_pages)>10000:
            raise ValueError('mock source exceeds row bound')
        cleaned.append({'member':asdict(member),'source':source,'outcome':outcome,'pages':clean_pages})
    document = capture_document('union_inputs',scope_id,captured_at,reference_version,
        {'input_kind':'mock_collection_script','mock_script_schema_version':1,'is_mock':True,'sources':cleaned})
    if len(document.serialized.encode())>16*1024*1024:
        raise ValueError('mock script exceeds byte bound')
    return document


class MockScriptCollection(MockCollection):
    def __init__(self, document, collection_inputs):
        data = document.payload
        if (document.envelope['kind'] != 'union_inputs' or document.envelope['scope_id'] != collection_inputs.envelope['scope_id']
                or document.envelope['reference_version'] != collection_inputs.envelope['reference_version']
                or set(data) != {'input_kind','mock_script_schema_version','is_mock','sources'}
                or data['input_kind'] != 'mock_collection_script' or type(data['mock_script_schema_version']) is not int
                or data['mock_script_schema_version'] != 1 or data['is_mock'] is not True
                or capture_mock_collection_script(document.envelope['scope_id'],document.envelope['captured_at'],document.envelope['reference_version'],data['sources']) != document):
            raise ValueError('verified scoped synthetic collection script required')
        members = {MemberKey(**member) for member in collection_inputs.payload['members']}
        supplied = {(MemberKey(**item['member']),item['source']) for item in data['sources']}
        if supplied != {(member,source) for member in members for source in SOURCES}:
            raise ValueError('mock script must cover exactly the collection member sources')
        pages = {}
        for item in data['sources']:
            member, source = MemberKey(**item['member']),item['source']
            if item['outcome'] != 'pages':
                pages[(member.key,source,None)] = CollectionSourceError(item['outcome']=='auth_required')
                continue
            revision = _cursor(document.snapshot_id,member,source,-1)
            for index,page in enumerate(item['pages']):
                cursor = None if index==0 else _cursor(document.snapshot_id,member,source,index)
                following = None if index==len(item['pages'])-1 else _cursor(document.snapshot_id,member,source,index+1)
                pages[(member.key,source,cursor)] = {**page,'member':asdict(member),'source':source,
                    'scope_id':document.envelope['scope_id'],'reference_version':document.envelope['reference_version'],
                    'source_revision':revision,'next_cursor':following}
        # An immutable script is a complete stable-revision source by construction.
        super().__init__(pages,is_stable_snapshot_revision=True)


def _cursor(script_id, member, source, index):
    return hashlib.sha256(canonical_json([script_id,member.key,source,index]).encode()).hexdigest()
