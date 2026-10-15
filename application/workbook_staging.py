"""Pinned workbook stage manifests for the offline publisher."""

from dataclasses import asdict, dataclass
import hashlib

from application.tasks import StateConflict, validate_identifier
from domain.chunks import ChunkManifest, encode_document
from domain.snapshots import canonical_json, capture_document


@dataclass(frozen=True)
class WorkbookStage:
    resource: str
    revision: str
    job_id: str
    result_id: str
    commit_id: str
    base_commit_id: str | None
    manifest: ChunkManifest

    def __post_init__(self):
        for value in (self.resource, self.revision, self.job_id, self.result_id, self.commit_id):
            validate_identifier(value)
        if self.base_commit_id is not None:
            validate_identifier(self.base_commit_id)
        if not isinstance(self.manifest, ChunkManifest):
            raise ValueError("typed stage manifest required")

    @property
    def serialized(self):
        return canonical_json(asdict(self))

    @property
    def stage_id(self):
        return hashlib.sha256(self.serialized.encode()).hexdigest()


def prepare_workbook_stage(store, sheets, claim, resource: str):
    job = store.get_job(claim.job_id)
    result = store.get_result(job.result_id)
    if resource not in claim.resources or set(result["workbooks"]) != set(claim.resources):
        raise StateConflict("stage differs from publication resources")
    book = sheets.read(resource)
    revision = result["layout_revisions"][resource]
    if book["layout_revision"] != revision:
        raise StateConflict("stage workbook schema differs")
    store.record_resource_observation(claim, resource, sheets.observe(resource, result["workbooks"][resource], claim.commit_id))
    document = capture_document("union_inputs", resource, "1970-01-01T00:00:00Z", job.spec.code_sha,
        {"staging_kind": "workbook_generated", "generated": result["workbooks"][resource]})
    manifest, chunks = encode_document(document)
    return WorkbookStage(resource, revision, job.job_id, job.result_id, claim.commit_id, book["commit_id"], manifest), chunks


class StagedSheets:
    def __init__(self, store, sheets, staging, stages):
        self.store, self.sheets, self.staging = store, sheets, staging
        self.stages = dict(stages)

    def observe(self, resource, generated, commit_id):
        return self.sheets.observe(resource, generated, commit_id)

    def apply(self, resource, revision, generated, commit_id, fault=None):
        stage = self.staging.stage(self.stages[resource])
        job = self.store.get_job(stage.job_id)
        if (stage.resource != resource or stage.revision != revision or stage.commit_id != commit_id
                or job.state != "publishing" or job.result_id != stage.result_id
                or canonical_json(self.staging.payload(stage.stage_id)) != canonical_json(generated)):
            raise StateConflict("stage differs from sealed publication")
        self.staging.activate(stage.stage_id, self.sheets, fault)
