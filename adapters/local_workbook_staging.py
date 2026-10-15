"""Local staged workbook payloads. Unknown activation requires explicit reconciliation."""

import json

from adapters.local_chunks import LocalChunkStore
from application.tasks import StateConflict
from application.workbook_staging import WorkbookStage
from domain.chunks import ChunkManifest


class LocalWorkbookStaging(LocalChunkStore):
    def __init__(self, path):
        super().__init__(path)
        with self.connection:
            self.connection.execute("CREATE TABLE IF NOT EXISTS workbook_stages(stage_id TEXT PRIMARY KEY, descriptor TEXT NOT NULL, state TEXT NOT NULL)")

    def begin_stage(self, stage: WorkbookStage):
        self.begin(stage.manifest)
        with self.connection:
            self.connection.execute("INSERT OR IGNORE INTO workbook_stages VALUES(?,?,'preparing')", (stage.stage_id, stage.serialized))
            self.stage(stage.stage_id)

    def stage(self, stage_id):
        row = self.connection.execute("SELECT descriptor FROM workbook_stages WHERE stage_id=?", (stage_id,)).fetchone()
        if row is None:
            raise StateConflict("unknown workbook stage")
        data = json.loads(row[0])
        stage = WorkbookStage(**{**data, "manifest": ChunkManifest(**data["manifest"])})
        if stage.stage_id != stage_id:
            raise StateConflict("stage descriptor corrupted")
        return stage

    def state(self, stage_id):
        self.stage(stage_id)
        return self.connection.execute("SELECT state FROM workbook_stages WHERE stage_id=?", (stage_id,)).fetchone()[0]

    def put_stage_chunk(self, stage_id, ordinal, chunk):
        stage = self.stage(stage_id)
        if self.state(stage_id) not in ("preparing", "sealed"):
            raise StateConflict("stage no longer accepts chunks")
        self.put(stage.manifest.object_id, ordinal, chunk)

    def seal_stage(self, stage_id):
        stage = self.stage(stage_id)
        if self.state(stage_id) not in ("preparing", "sealed"):
            raise StateConflict("stage cannot be sealed")
        self.seal(stage.manifest.object_id)
        self._set_state(stage_id, ("preparing", "sealed"), "sealed")

    def payload(self, stage_id):
        stage = self.stage(stage_id)
        if self.state(stage_id) not in ("sealed", "activation_uncertain", "activated"):
            raise StateConflict("stage payload is not available for activation")
        document = self.read(stage.manifest.object_id)
        if document.envelope["scope_id"] != stage.resource or document.payload.get("staging_kind") != "workbook_generated":
            raise StateConflict("stage object scope differs")
        return document.payload["generated"]

    def cancel_stage(self, stage_id):
        self._set_state(stage_id, ("preparing", "sealed", "cancelled"), "cancelled")

    def activate(self, stage_id, sheets, fault=None):
        stage = self.stage(stage_id)
        generated = self.payload(stage_id)
        if self.state(stage_id) == "activated":
            if sheets.observe(stage.resource, generated, stage.commit_id) != stage.commit_id:
                raise StateConflict("activated stage content no longer matches")
            return
        if self.state(stage_id) != "sealed":
            raise StateConflict("uncertain activation requires explicit reconciliation")
        book = sheets.read(stage.resource)
        if book["layout_revision"] != stage.revision or book["commit_id"] != stage.base_commit_id:
            raise StateConflict("workbook changed since stage capture")
        # Persist unknown before external write; a vanished caller never grants an automatic retry.
        self._set_state(stage_id, ("sealed",), "activation_uncertain")
        sheets.apply(stage.resource, stage.revision, generated, stage.commit_id, fault)
        if sheets.observe(stage.resource, generated, stage.commit_id) != stage.commit_id:
            raise StateConflict("staged activation readback differs")
        self._set_state(stage_id, ("activation_uncertain",), "activated")

    def reconcile_stage(self, stage_id, sheets, is_writer_stopped):
        if is_writer_stopped is not True or self.state(stage_id) != "activation_uncertain":
            raise StateConflict("stopped uncertain stage writer required")
        stage = self.stage(stage_id)
        if sheets.observe(stage.resource, self.payload(stage_id), stage.commit_id) == stage.commit_id:
            self._set_state(stage_id, ("activation_uncertain",), "activated")
        else:
            # Absence cannot be inferred from another commit or marker alone.
            raise StateConflict("stage activation is not verified")

    def _set_state(self, stage_id, expected, state):
        self.stage(stage_id)
        with self.connection:
            cursor = self.connection.execute("UPDATE workbook_stages SET state=? WHERE stage_id=? AND state IN (" +
                ",".join("?" for _ in expected) + ")", (state, stage_id, *expected))
            if cursor.rowcount != 1:
                raise StateConflict("stage state changed")
