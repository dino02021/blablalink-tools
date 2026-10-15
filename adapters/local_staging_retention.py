"""Explicit, bounded cleanup of proven terminal mock workbook stages only."""

from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os

from adapters.local_state import LocalStateStore, _transaction
from adapters.local_workbook_staging import LocalWorkbookStaging
from adapters.mock_workspace import require_mock_workspace
from application.tasks import StateConflict
from domain.chunks import ChunkManifest, verify_chunk
from domain.execution import check_execution
from domain.snapshots import canonical_json, capture_document


@dataclass(frozen=True)
class StagingRetentionPlan:
    workspace_id: str
    cutoff: float
    limit: int
    inventory_hash: str
    stage_ids: tuple[str, ...]
    object_ids: tuple[str, ...]
    chunk_bytes: int
    schema_version: int = 1

    def __post_init__(self):
        stages, objects = tuple(self.stage_ids), tuple(self.object_ids)
        if (type(self.schema_version) is not int or self.schema_version != 1
                or type(self.cutoff) not in (int, float) or not math.isfinite(self.cutoff)
                or type(self.limit) is not int or not 1 <= self.limit <= 100
                or len(stages) > self.limit or len(objects) > len(stages)
                or stages != tuple(sorted(set(stages))) or objects != tuple(sorted(set(objects)))
                or type(self.chunk_bytes) is not int or not 0 <= self.chunk_bytes <= 64*1024*1024):
            raise ValueError('bounded fixed staging retention plan required')
        for value in (self.workspace_id, self.inventory_hash, *stages, *objects):
            if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
                raise ValueError('retention SHA-256 required')
        object.__setattr__(self, 'stage_ids', stages)
        object.__setattr__(self, 'object_ids', objects)

    @property
    def plan_id(self):
        return _hash(asdict(self))


class LocalStagingRetention:
    def __init__(self, directory):
        self.directory = require_mock_workspace(directory, is_writer=True)
        path = self.directory/'staging.sqlite'
        if (not path.is_file() or path.is_symlink() or (self.directory/'state.sqlite').is_symlink()
                or path.stat().st_size > 64*1024*1024):
            raise ValueError('existing bounded local staging database required')
        self.workspace_id = _hash(os.path.normcase(str(self.directory)))
        self.store = LocalStateStore(self.directory/'state.sqlite')
        try:
            self.staging = LocalWorkbookStaging(path)
            with self.staging.connection:
                self.staging.connection.execute('CREATE TABLE IF NOT EXISTS staging_retention_receipts('
                    'plan_id TEXT PRIMARY KEY, plan_json TEXT NOT NULL, receipt_json TEXT NOT NULL)')
        except BaseException:
            self.store.close()
            raise

    def close(self):
        self.staging.close()
        self.store.close()

    def plan_retention(self, cutoff, limit=100, is_all_writers_stopped=False):
        StagingRetentionPlan(self.workspace_id, cutoff, limit, '0'*64, (), (), 0)
        with self._locked(is_all_writers_stopped):
            return self._plan(cutoff, limit)

    def apply_retention(self, plan, confirmed_plan_id, is_all_writers_stopped=False):
        if not isinstance(plan, StagingRetentionPlan) or confirmed_plan_id != plan.plan_id or plan.workspace_id != self.workspace_id:
            raise StateConflict('exact local staging retention confirmation required')
        with self._locked(is_all_writers_stopped):
            prior = self.staging.connection.execute('SELECT plan_json,receipt_json FROM staging_retention_receipts WHERE plan_id=?', (plan.plan_id,)).fetchone()
            body = {'plan_id':plan.plan_id, 'stage_ids':list(plan.stage_ids), 'object_ids':list(plan.object_ids),
                'deleted_chunk_bytes':plan.chunk_bytes, 'is_mock':True}
            receipt = {**body, 'receipt_id':_hash(body)}
            if prior:
                if prior[0] != canonical_json(asdict(plan)) or prior[1] != canonical_json(receipt):
                    raise StateConflict('staging retention receipt differs')
                return receipt
            if self._plan(plan.cutoff, plan.limit) != plan:
                raise StateConflict('staging inventory or terminal evidence changed')
            for stage_id in plan.stage_ids:
                check_execution()
                self.staging.connection.execute('DELETE FROM workbook_stages WHERE stage_id=?', (stage_id,))
            for object_id in plan.object_ids:
                check_execution()
                self.staging.connection.execute('DELETE FROM object_chunks WHERE object_id=?', (object_id,))
                self.staging.connection.execute('DELETE FROM chunk_manifests WHERE object_id=?', (object_id,))
            self.staging.connection.execute('INSERT INTO staging_retention_receipts VALUES(?,?,?)',
                (plan.plan_id, canonical_json(asdict(plan)), canonical_json(receipt)))
            return receipt

    @contextmanager
    def _locked(self, is_all_writers_stopped):
        require_mock_workspace(self.directory, is_writer=True)
        if is_all_writers_stopped is not True:
            raise StateConflict('all local staging and coordinator writers must be stopped')
        # Root first, staging second. Only the staging database is mutated, so deletion
        # and its receipt commit atomically without a cross-database commit protocol.
        with _transaction(self.store._connection):
            self.store._require_no_publisher()
            if self.store._connection.execute("SELECT 1 FROM jobs WHERE state IN ('running','dispatched','publishing','publish_uncertain') LIMIT 1").fetchone():
                raise StateConflict('active or uncertain executors prevent staging cleanup')
            self.staging.connection.execute('BEGIN IMMEDIATE')
            try:
                yield
                self.staging.connection.commit()
            except BaseException:
                self.staging.connection.rollback()
                raise

    def _plan(self, cutoff, limit):
        check_execution()
        connection = self.staging.connection
        sizes = [connection.execute(sql).fetchone()[0] or 0 for sql in (
            'SELECT SUM(length(CAST(descriptor AS BLOB))) FROM workbook_stages',
            'SELECT SUM(length(CAST(manifest AS BLOB))) FROM chunk_manifests',
            'SELECT SUM(length(content)) FROM object_chunks')]
        if sum(sizes)>64*1024*1024:
            raise ValueError('staging inventory exceeds byte bound')
        stage_rows = connection.execute('SELECT stage_id,descriptor,state FROM workbook_stages ORDER BY stage_id LIMIT 10001').fetchall()
        manifest_rows = connection.execute('SELECT object_id,manifest,sealed FROM chunk_manifests ORDER BY object_id LIMIT 10001').fetchall()
        chunk_rows = connection.execute('SELECT object_id,ordinal,content FROM object_chunks ORDER BY object_id,ordinal LIMIT 20001').fetchall()
        if len(stage_rows)>10000 or len(manifest_rows)>10000 or len(chunk_rows)>20000:
            raise ValueError('staging inventory exceeds row bound')
        total = sum(len(row[1].encode()) for row in stage_rows+manifest_rows) + sum(len(row[2]) for row in chunk_rows)
        if total > 64*1024*1024:
            raise ValueError('staging inventory exceeds byte bound')
        manifests, object_bytes, chunk_hashes = {}, {}, []
        for object_id, serialized, sealed in manifest_rows:
            check_execution()
            manifest = ChunkManifest(**json.loads(serialized))
            if manifest.object_id != object_id or type(sealed) is not int or sealed not in (0,1):
                raise StateConflict('staging manifest identity differs')
            manifests[object_id] = manifest
        if sum(manifest.raw_size for manifest in manifests.values())>64*1024*1024:
            raise ValueError('staging decoded inventory exceeds byte bound')
        for object_id, ordinal, content in chunk_rows:
            check_execution()
            if object_id not in manifests:
                raise StateConflict('chunk has no manifest')
            verify_chunk(manifests[object_id], ordinal, content)
            chunk_hashes.append((object_id,ordinal,hashlib.sha256(content).hexdigest()))
            object_bytes[object_id] = object_bytes.get(object_id,0)+len(content)
        stages, terminal, eligible = {}, [], []
        for stage_id, descriptor, state in stage_rows:
            check_execution()
            stage = self.staging.stage(stage_id)
            if stage.manifest.object_id not in manifests or stage.manifest != manifests[stage.manifest.object_id] or state not in ('preparing','sealed','activated','cancelled','activation_uncertain'):
                raise StateConflict('stage manifest or state differs')
            if state in ('sealed','activated','activation_uncertain'):
                self.staging.read(stage.manifest.object_id)
            stages[stage_id] = stage
            rows = self.store._connection.execute('SELECT p.publication_id,p.effective_state,p.updated_at,j.state,j.result_id '
                'FROM publication_effective_states p JOIN jobs j ON j.job_id=p.job_id WHERE p.job_id=? AND p.commit_id=? ORDER BY p.publication_id LIMIT 2', (stage.job_id,stage.commit_id)).fetchall()
            evidence = [tuple(row) for row in rows]
            terminal.append((stage_id,evidence))
            if len(rows)!=1:continue
            publication_id, effective, updated_at, job_state, result_id = rows[0]
            if (effective not in ('succeeded','aborted') or job_state not in ('succeeded','failed','cancelled','superseded')
                    or result_id!=stage.result_id or updated_at>cutoff or state=='activation_uncertain'
                    or state=='activated' and effective!='succeeded'):
                continue
            # The coordinator keeps the sealed original result; this only drops
            # the transport copy, not publication history or generated workbooks.
            result = self.store.get_result(result_id)
            generated = result['workbooks'][stage.resource]
            if result['layout_revisions'][stage.resource]!=stage.revision:
                raise StateConflict('stage revision differs from terminal publication')
            job = self.store.get_job(stage.job_id)
            expected = capture_document('union_inputs',stage.resource,'1970-01-01T00:00:00Z',job.spec.code_sha,
                {'staging_kind':'workbook_generated','generated':generated})
            if expected.snapshot_id!=stage.manifest.object_id:
                raise StateConflict('stage content differs from sealed terminal result')
            eligible.append((updated_at,stage_id))
        selected = tuple(sorted(stage_id for _,stage_id in sorted(eligible)[:limit]))
        remaining_objects = {stage.manifest.object_id for stage_id,stage in stages.items() if stage_id not in selected}
        objects = tuple(sorted({stages[stage_id].manifest.object_id for stage_id in selected}-remaining_objects))
        inventory = {'stages':stage_rows,'manifests':manifest_rows,'chunks':chunk_hashes,'terminal':terminal}
        return StagingRetentionPlan(self.workspace_id,cutoff,limit,_hash(inventory),selected,objects,
            sum(object_bytes.get(object_id,0) for object_id in objects))


def _hash(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()
