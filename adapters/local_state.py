"""Durable local coordinator. SQLite tests are not proof of remote D1 behavior."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path
from typing import Callable, Mapping

from application.tasks import (
    AUTO_COMMANDS, COMMANDS, BudgetExceeded, BudgetNeed, ComputeLease, ConfigPublication, Dispatch, Job,
    LeaseLost, PublicationClaim, ResourceBusy, StateConflict, TaskSpec, validate_identifier,
)
from domain.snapshots import Document, canonical_json, read_config, restore_document


class LocalStateStore:
    def __init__(self, path: str | Path, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._connection = sqlite3.connect(str(path), timeout=5, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute("PRAGMA busy_timeout=5000")
        try:
            self._connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, checksum TEXT NOT NULL, applied_at REAL NOT NULL)"
            )
            folder = Path(__file__).resolve().parents[1] / "coordination" / "local_migrations"
            migrations = sorted(folder.glob("[0-9][0-9][0-9][0-9]_*.sql"))
            if not migrations:
                raise StateConflict("local migration files are missing")
            for migration in migrations:
                _run_migration(self._connection, migration, self._now())
        except BaseException:
            self._connection.close()
            raise

    def close(self) -> None:
        self._connection.close()

    def put_document(self, document: Document) -> str:
        with _transaction(self._connection):
            return self._put_object(document.serialized)

    def get_document(self, object_id: str) -> Document:
        return restore_document(self._get_serialized(object_id), object_id)

    def get_result(self, object_id: str) -> dict:
        return json.loads(self._get_serialized(object_id))

    def submit_job(self, request_id: str, spec: TaskSpec, is_desired: bool = False) -> Job:
        with _transaction(self._connection):
            return self._enqueue(request_id, spec, is_desired)

    def get_job(self, job_id: str) -> Job:
        return _job(self._job_row(job_id))

    def is_job_current(self, job_id: str) -> bool:
        return self._is_current(self._job_row(job_id))

    def supersede_if_stale(self, lease: ComputeLease) -> Job | None:
        with _transaction(self._connection):
            row = self._require_lease(lease)
            if self._is_current(row):
                return None
            self._transition(lease.job_id, "superseded", is_clear_lease=True)
            return self.get_job(lease.job_id)

    def has_successful_publication(self, job_id: str) -> bool:
        job = self.get_job(job_id)
        return job.state == "succeeded" and self._connection.execute(
            "SELECT 1 FROM publications WHERE job_id=? AND state='succeeded' LIMIT 1", (job_id,)
        ).fetchone() is not None

    def union_input_version(self, scope_id: str) -> dict | None:
        validate_identifier(scope_id)
        row = self._connection.execute("SELECT * FROM union_input_heads WHERE scope_id=?", (scope_id,)).fetchone()
        return dict(row) if row else None

    def raid_source_version(self, scope_id: str) -> dict | None:
        validate_identifier(scope_id)
        row = self._connection.execute("SELECT * FROM raid_source_heads WHERE scope_id=?", (scope_id,)).fetchone()
        if row is None:
            return None
        document = self.get_document(row["bundle_id"])
        if (document.envelope["kind"] != "union_inputs" or document.envelope["scope_id"] != scope_id
                or document.payload.get("input_kind") != "raid_source_bundle"):
            raise StateConflict("raid source head differs from immutable bundle")
        return {**document.payload, **dict(row)}

    def list_jobs(self) -> tuple[Job, ...]:
        return tuple(_job(row) for row in self._connection.execute("SELECT * FROM jobs ORDER BY created_at, job_id"))

    def computed_publication_jobs(self, code_sha: str, limit: int = 10) -> tuple[Job, ...]:
        validate_identifier(code_sha)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("bounded computed publication count required")
        return tuple(_job(row) for row in self._connection.execute(
            "SELECT * FROM jobs WHERE state='computed' AND code_sha=? AND executor_type='local' "
            "AND command IN ('recompute_healthcheck','publish_raid_plan','publish_system_status') ORDER BY created_at,job_id LIMIT ?", (code_sha,limit)))

    def reject_computed_result(self, job_id: str, code_sha: str) -> Job:
        with _transaction(self._connection):
            row = self._job_row(job_id)
            if row["code_sha"] != code_sha:
                raise StateConflict("computed result release differs")
            if row["state"] != "computed":
                return self.get_job(job_id)  # another publisher may already have claimed it
            if self._connection.execute("SELECT 1 FROM publication_effective_states WHERE job_id=? AND effective_state IN ('publishing','publish_uncertain')", (job_id,)).fetchone():
                raise StateConflict("computed result has unresolved publication evidence")
            self._transition(job_id, "failed", "worker_result_invalid", is_clear_lease=True)
            return self.get_job(job_id)

    def events(self, job_id: str) -> tuple[dict, ...]:
        self._job_row(job_id)
        return tuple(dict(row) for row in self._connection.execute(
            "SELECT state,error_code,created_at FROM job_events WHERE job_id=? ORDER BY event_id", (job_id,)
        ))

    def create_candidate(self, document: Document, base_config_id: str | None) -> str:
        read_config(document)
        scope = document.envelope["scope_id"]
        candidate_id = _digest({"document": document.snapshot_id, "base": base_config_id})
        semantic_id = _candidate_semantic(document, base_config_id)
        with _transaction(self._connection):
            existing = self._connection.execute("SELECT candidate_id FROM config_candidates WHERE candidate_id=? OR semantic_id=?",
                                                (candidate_id, semantic_id)).fetchone()
            if existing:
                return existing[0]
            legacy = self._connection.execute("SELECT candidate_id,document_id FROM config_candidates "
                "WHERE semantic_id IS NULL AND config_type='healthcheck' AND scope_id=? AND base_config_id IS ?",
                (scope, base_config_id)).fetchall()
            for row in legacy:
                if _candidate_semantic(self.get_document(row["document_id"]), base_config_id) == semantic_id:
                    self._connection.execute("UPDATE config_candidates SET semantic_id=? WHERE candidate_id=?", (semantic_id, row["candidate_id"]))
                    return row["candidate_id"]
            if self.config_head("healthcheck", scope) != base_config_id:
                raise StateConflict("candidate base is not the current configuration")
            self._put_object(document.serialized)
            self._connection.execute(
                "INSERT OR IGNORE INTO config_candidates(candidate_id,config_type,scope_id,document_id,base_config_id,created_at,semantic_id) "
                "VALUES(?,?,?,?,?,?,?)", (candidate_id, "healthcheck", scope, document.snapshot_id, base_config_id, self._now(), semantic_id),
            )
        return candidate_id

    def candidate_document(self, candidate_id: str) -> Document:
        row = self._connection.execute("SELECT document_id FROM config_candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
        if not row:
            raise StateConflict("unknown candidate")
        return self.get_document(row[0])

    def config_head(self, config_type: str, scope_id: str) -> str | None:
        row = self._connection.execute(
            "SELECT config_id FROM config_heads WHERE config_type=? AND scope_id=?", (config_type, scope_id)
        ).fetchone()
        return row["config_id"] if row else None

    def publish_candidate(self, candidate_id: str, targets: tuple[TaskSpec, ...]) -> ConfigPublication:
        targets = tuple(targets)
        if any(spec.command != "recompute_healthcheck" for spec in targets):
            raise ValueError("configuration publication only fans out healthcheck recomputation")
        if len({(spec.scope_id, spec.family) for spec in targets}) != len(targets):
            raise ValueError("duplicate target family")
        target_signature = _digest([asdict(spec) for spec in sorted(targets, key=lambda item: (item.scope_id, item.family))])
        with _transaction(self._connection):
            candidate = self._connection.execute(
                "SELECT * FROM config_candidates WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
            if candidate is None:
                raise KeyError("unknown candidate")
            if candidate["published_at"] is not None:
                if candidate["target_signature"] != target_signature:
                    raise StateConflict("published candidate target set changed")
                return self._config_publication(candidate)
            self._require_no_publisher()
            if self.config_head(candidate["config_type"], candidate["scope_id"]) != candidate["base_config_id"]:
                raise StateConflict("configuration changed after candidate capture")
            config_id = candidate["document_id"]
            self._connection.execute(
                "INSERT OR IGNORE INTO config_versions VALUES(?,?,?,?,?)",
                (config_id, candidate_id, candidate["config_type"], candidate["scope_id"], self._now()),
            )
            self._connection.execute(
                "INSERT INTO config_heads VALUES(?,?,?,1) ON CONFLICT(config_type,scope_id) "
                "DO UPDATE SET config_id=excluded.config_id,revision=revision+1",
                (candidate["config_type"], candidate["scope_id"], config_id),
            )
            for spec in targets:
                versions = spec.version_map
                if "healthcheck_config" in versions and versions["healthcheck_config"] != config_id:
                    raise StateConflict("target specifies a different configuration")
                versions["healthcheck_config"] = config_id
                bound = replace(spec, versions=versions)
                job = self._enqueue(f"config:{candidate_id}:{spec.scope_id}:{spec.family}", bound, True)
                self._connection.execute("INSERT INTO candidate_jobs VALUES(?,?)", (candidate_id, job.job_id))
            self._connection.execute(
                "UPDATE config_candidates SET published_at=?,target_signature=? WHERE candidate_id=?",
                (self._now(), target_signature, candidate_id),
            )
            return self._config_publication(candidate)

    def claim(self, worker_id: str, code_sha: str, executor_type: str = "local",
              lease_seconds: float = 60,
              budget: BudgetNeed | tuple[BudgetNeed, ...] | None = None,
              dispatch_id: str | None = None, executor_run_id: str | None = None,
              commands: tuple[str, ...] | None = None,
              required_versions: Mapping[str, tuple[str, ...]] | None = None) -> ComputeLease | None:
        validate_identifier(worker_id)
        validate_identifier(code_sha)
        _validate_duration(lease_seconds)
        if commands is not None:
            if (isinstance(commands, str) or not commands or len(set(commands)) != len(commands)
                    or any(command not in COMMANDS for command in commands)):
                raise ValueError("nonempty unique supported commands required")
            commands = tuple(commands)
        if required_versions is not None and (not isinstance(required_versions, dict) or len(required_versions) > len(COMMANDS)):
            raise ValueError("bounded command version requirements required")
        if any(not isinstance(command, str) or command not in COMMANDS for command in (required_versions or {})):
            raise ValueError("supported capability command required")
        capability_filter, capability_args = "", []
        for command, keys in sorted((required_versions or {}).items()):
            if (command not in COMMANDS or commands is not None and command not in commands
                    or not isinstance(keys, (tuple, list)) or not 1 <= len(keys) <= 16
                    or any(not isinstance(key, str) or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key) is None for key in keys) or len(set(keys)) != len(keys)):
                raise ValueError("supported commands and unique bounded version keys required")
            capability_filter += " AND (command!=? OR (" + " AND ".join(
                "EXISTS(SELECT 1 FROM json_each(j.spec_json,'$.versions') v WHERE json_extract(v.value,'$[0]')=?)" for key in keys) + "))"
            capability_args.extend((command, *keys))
        needs = _budget_needs(executor_type, budget) if executor_type == "local" else ()
        if executor_type != "local":
            if commands is not None or required_versions is not None:
                raise ValueError("command filters apply only to local queue claims")
            if executor_type not in ("vercel", "actions") or not dispatch_id or not executor_run_id or budget is not None:
                raise ValueError("cloud claims require a pre-budgeted dispatch and executor run ID")
            with _transaction(self._connection):
                return self._claim_dispatch(dispatch_id, worker_id, code_sha, executor_type, executor_run_id, lease_seconds)
        if dispatch_id is not None or executor_run_id is not None:
            raise ValueError("local claims do not use hosted dispatch callbacks")
        with _transaction(self._connection):
            self._recover()
            command_filter = " AND command IN (" + ",".join("?" for _ in commands) + ")" if commands else ""
            rows = self._connection.execute(
                "SELECT * FROM jobs j WHERE state IN ('queued','retry_wait') AND next_attempt_at<=? "
                "AND attempt<max_attempts AND code_sha=? AND executor_type=? AND NOT EXISTS("
                "SELECT 1 FROM job_dependencies d JOIN jobs p ON p.job_id=d.parent_id "
                "WHERE d.job_id=j.job_id AND p.state!='succeeded')" + command_filter + capability_filter + " ORDER BY priority DESC,created_at,job_id LIMIT 100",
                (self._now(), code_sha, executor_type, *(commands or ()), *capability_args),
            ).fetchall()
            for row in rows:
                if not self._is_current(row):
                    self._transition(row["job_id"], "superseded")
                    continue
                generation = row["generation"] + 1
                if needs:
                    try:
                        self._reserve_claim_budget(row["job_id"], generation, needs)
                    except BudgetExceeded:
                        self._transition(row["job_id"], "capacity_blocked", "budget_exhausted")
                        continue
                self._connection.execute(
                    "UPDATE jobs SET owner=?,generation=?,expires_at=?,attempt=attempt+1 WHERE job_id=?",
                    (worker_id, generation, self._now() + lease_seconds, row["job_id"]),
                )
                self._transition(row["job_id"], "running")
                return ComputeLease(row["job_id"], worker_id, code_sha, generation)
        return None

    def dispatch(self, job_id: str, dispatcher_id: str,
                 budget: BudgetNeed | tuple[BudgetNeed, ...], timeout_seconds: float = 300) -> Dispatch | None:
        validate_identifier(dispatcher_id)
        _validate_duration(timeout_seconds)
        with _transaction(self._connection):
            self._recover()
            row = self._job_row(job_id)
            if row["executor_type"] not in ("actions", "vercel"):
                raise ValueError("local work does not require hosted dispatch")
            needs = _budget_needs(row["executor_type"], budget)
            budget_json = canonical_json([asdict(need) for need in needs])
            if row["state"] == "dispatched":
                current = self._connection.execute(
                    "SELECT * FROM dispatches WHERE job_id=? AND state='pending'", (job_id,)
                ).fetchone()
                if current and current["dispatcher_id"] == dispatcher_id and current["budget_json"] == budget_json:
                    return _dispatch(current)
                raise StateConflict("dispatch intent changed")
            if row["state"] not in ("queued", "retry_wait") or row["next_attempt_at"] > self._now():
                raise StateConflict("job is not ready to dispatch")
            if not self._is_current(row):
                self._transition(job_id, "superseded")
                return None
            unresolved = self._connection.execute(
                "SELECT 1 FROM job_dependencies d JOIN jobs p ON p.job_id=d.parent_id "
                "WHERE d.job_id=? AND p.state!='succeeded' LIMIT 1", (job_id,)
            ).fetchone()
            if unresolved:
                raise StateConflict("dispatch dependencies are not complete")
            count = self._connection.execute("SELECT COUNT(*) FROM dispatches WHERE job_id=?", (job_id,)).fetchone()[0]
            if count >= row["max_attempts"]:
                self._transition(job_id, "failed", "dispatch_limit_exceeded")
                return None
            dispatch_id = uuid.uuid4().hex
            try:
                reservation_ids = self._reserve_claim_budget(job_id, dispatch_id, needs)
            except BudgetExceeded:
                self._transition(job_id, "capacity_blocked", "budget_exhausted")
                return None
            self._connection.execute(
                "INSERT INTO dispatches VALUES(?,?,?,?,?,?,'pending',NULL,NULL,NULL,?,?)",
                (dispatch_id, job_id, dispatcher_id, row["executor_type"], row["code_sha"], budget_json,
                 self._now(), self._now() + timeout_seconds),
            )
            self._connection.executemany(
                "INSERT INTO dispatch_budgets VALUES(?,?)", ((dispatch_id, reservation_id) for reservation_id in reservation_ids)
            )
            self._transition(job_id, "dispatched")
            return _dispatch(self._connection.execute("SELECT * FROM dispatches WHERE dispatch_id=?", (dispatch_id,)).fetchone())

    def heartbeat(self, lease: ComputeLease, lease_seconds: float = 60) -> None:
        _validate_duration(lease_seconds)
        with _transaction(self._connection):
            self._require_lease(lease)
            self._connection.execute(
                "UPDATE jobs SET expires_at=?,updated_at=? WHERE job_id=?",
                (self._now() + lease_seconds, self._now(), lease.job_id),
            )

    def complete_compute(self, lease: ComputeLease, result: dict, is_publication_required: bool = True) -> Job:
        if not isinstance(result, dict) or not isinstance(is_publication_required, bool):
            raise ValueError("invalid computation result")
        serialized = canonical_json(result)
        with _transaction(self._connection):
            row = self._require_lease(lease)
            result_id = self._put_object(serialized)
            if row["result_id"] is not None and row["result_id"] != result_id:
                raise StateConflict("same immutable task produced a different result")
            self._connection.execute("UPDATE jobs SET result_id=? WHERE job_id=?", (result_id, lease.job_id))
            state = "computed" if is_publication_required else "succeeded"
            if not self._is_current(row):
                state = "superseded"
            self._transition(lease.job_id, state, is_clear_lease=True)
            return self.get_job(lease.job_id)

    def save_checkpoint(self, lease: ComputeLease, checkpoint: dict) -> str:
        if not isinstance(checkpoint, dict):
            raise ValueError("checkpoint must be an object")
        serialized = canonical_json(checkpoint)
        with _transaction(self._connection):
            self._require_lease(lease)
            object_id = self._put_object(serialized)
            self._connection.execute(
                "INSERT INTO job_checkpoints VALUES(?,?,?,?) ON CONFLICT(job_id) "
                "DO UPDATE SET object_id=excluded.object_id,generation=excluded.generation,updated_at=excluded.updated_at",
                (lease.job_id, object_id, lease.generation, self._now()),
            )
            return object_id

    def load_checkpoint(self, job_id: str) -> dict | None:
        self._job_row(job_id)
        row = self._connection.execute("SELECT object_id FROM job_checkpoints WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            return None
        result = self.get_result(row["object_id"])
        if result.get("checkpoint_kind") in ("training_collection", "training_collection_pointer"):
            progress = self.load_collection_progress(job_id)
            if progress is None:
                raise StateConflict("collection progress is missing")
            if result["checkpoint_kind"] == "training_collection_pointer":
                expected = {"schema_version": 1, "checkpoint_kind": "training_collection_pointer", "progress_job_id": job_id,
                    "collection_inputs_id": progress["collection_inputs_id"], "code_sha": progress["code_sha"]}
                if canonical_json(expected) != canonical_json(result):
                    raise StateConflict("collection progress pointer differs")
                return progress
            if canonical_json(progress) != canonical_json(result):
                raise StateConflict("collection sealed progress differs")
        return result

    def save_collection_progress(self, lease: ComputeLease, metadata: dict, source_key: str | None = None, source_state: dict | None = None) -> str:
        if (not isinstance(metadata, dict) or "sources" in metadata or metadata.get("checkpoint_kind") != "training_collection"
                or (source_key is None) != (source_state is None)
                or source_key is not None and (not isinstance(source_key, str) or len(source_key) > 4096 or not isinstance(source_state, dict))):
            raise ValueError("explicit collection progress fields required")
        serialized = canonical_json(metadata)
        state_json = canonical_json(source_state) if source_state is not None else None
        if len(serialized.encode()) > 4096 or state_json is not None and len(state_json.encode()) > 32768:
            raise ValueError("collection progress metadata exceeds bound")
        with _transaction(self._connection):
            row = self._require_lease(lease)
            if (row["command"] != "refresh_roster_and_training" or metadata.get("code_sha") != row["code_sha"]
                    or metadata.get("collection_inputs_id") != _job(row).spec.version_map.get("collection_inputs")):
                raise StateConflict("collection progress job dependency differs")
            self._connection.execute("INSERT INTO collection_progress VALUES(?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET "
                "metadata_json=excluded.metadata_json,metadata_hash=excluded.metadata_hash,generation=excluded.generation,updated_at=excluded.updated_at",
                (lease.job_id, serialized, _digest({"job_id": lease.job_id, "metadata": metadata}), lease.generation, self._now()))
            if source_key is not None:
                self._connection.execute("INSERT INTO collection_source_progress VALUES(?,?,?,?) ON CONFLICT(job_id,source_key) DO UPDATE SET state_json=excluded.state_json,state_hash=excluded.state_hash",
                    (lease.job_id, source_key, state_json, _digest({"job_id": lease.job_id, "source_key": source_key, "state": source_state})))
            pointer = {"schema_version": 1, "checkpoint_kind": "training_collection_pointer", "progress_job_id": lease.job_id,
                "collection_inputs_id": metadata["collection_inputs_id"], "code_sha": metadata["code_sha"]}
            object_id = self._put_object(canonical_json(pointer))
            self._connection.execute("INSERT INTO job_checkpoints VALUES(?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET object_id=excluded.object_id,generation=excluded.generation,updated_at=excluded.updated_at",
                (lease.job_id, object_id, lease.generation, self._now()))
            return object_id

    def save_collection_observation(self, lease: ComputeLease, checkpoint: dict) -> str:
        if (not isinstance(checkpoint, dict) or checkpoint.get("checkpoint_kind") != "training_collection"
                or not isinstance(checkpoint.get("last_observation"), dict)
                or set(checkpoint["last_observation"]) != {"raw_snapshot_id", "training_snapshot_id", "captured_at"}):
            raise ValueError("fixed collection observation required")
        serialized = canonical_json(checkpoint)
        if len(serialized.encode()) > 8*1024*1024:
            raise ValueError("collection observation manifest exceeds bound")
        with _transaction(self._connection):
            row = self._require_lease(lease)
            current = self.load_collection_progress(lease.job_id)
            if (row["command"] != "refresh_roster_and_training" or current is None
                    or current.get("code_sha") != row["code_sha"]
                    or current.get("collection_inputs_id") != _job(row).spec.version_map.get("collection_inputs")
                    or canonical_json({key: value for key, value in current.items() if key != "last_observation"})
                    != canonical_json({key: value for key, value in checkpoint.items() if key != "last_observation"})):
                raise StateConflict("collection observation does not match current progress")
            observation = checkpoint["last_observation"]
            raw = self.get_document(observation["raw_snapshot_id"])
            training = self.get_document(observation["training_snapshot_id"])
            inputs = self.get_document(current["collection_inputs_id"])
            if (raw.envelope["kind"] != "raw" or training.envelope["kind"] != "training"
                    or raw.envelope["scope_id"] != row["scope_id"] or training.envelope["scope_id"] != row["scope_id"]
                    or training.payload.get("raw_snapshot_id") != raw.snapshot_id
                    or raw.envelope["reference_version"] != training.envelope["reference_version"]
                    or raw.envelope["reference_version"] != inputs.envelope["reference_version"]
                    or raw.payload.get("reference_role_ids") != inputs.payload.get("role_ids")
                    or raw.payload.get("member_epochs", {}) != training.payload.get("member_epochs", {})
                    or raw.payload.get("member_epochs", {}) != inputs.payload.get("member_epochs", {})
                    or raw.envelope["captured_at"] != observation["captured_at"] or training.envelope["captured_at"] != observation["captured_at"]):
                raise StateConflict("collection observation snapshot lineage differs")
            object_id = self._put_object(serialized)
            prior = self._connection.execute("SELECT raw_snapshot_id,training_snapshot_id,checkpoint_id,captured_at FROM collection_observations WHERE job_id=? AND generation=?",
                (lease.job_id, lease.generation)).fetchone()
            values = (raw.snapshot_id, training.snapshot_id, object_id, observation["captured_at"])
            if prior is not None and tuple(prior) != values:
                raise StateConflict("collection generation already captured another observation")
            metadata = {key: value for key, value in checkpoint.items() if key != "sources"}
            self._connection.execute("UPDATE collection_progress SET metadata_json=?,metadata_hash=?,generation=?,updated_at=? WHERE job_id=?",
                (canonical_json(metadata), _digest({"job_id": lease.job_id, "metadata": metadata}), lease.generation, self._now(), lease.job_id))
            self._connection.execute("INSERT OR IGNORE INTO collection_observations VALUES(?,?,?,?,?,?)", (lease.job_id, lease.generation, *values))
            self._connection.execute("INSERT INTO job_checkpoints VALUES(?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET object_id=excluded.object_id,generation=excluded.generation,updated_at=excluded.updated_at",
                (lease.job_id, object_id, lease.generation, self._now()))
            return object_id

    def load_collection_progress(self, job_id: str) -> dict | None:
        self._job_row(job_id)
        row = self._connection.execute("SELECT * FROM collection_progress WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            return None
        metadata = json.loads(row["metadata_json"])
        if not isinstance(metadata, dict) or "sources" in metadata or row["metadata_hash"] != _digest({"job_id": job_id, "metadata": metadata}):
            raise StateConflict("collection progress metadata integrity differs")
        sources = self._connection.execute("SELECT * FROM collection_source_progress WHERE job_id=? ORDER BY source_key LIMIT 901", (job_id,)).fetchall()
        if len(sources) > 900 or sum(len(source["state_json"].encode()) for source in sources) > 8*1024*1024:
            raise StateConflict("collection progress inventory exceeds bound")
        result = {**metadata, "sources": {}}
        for source in sources:
            state = json.loads(source["state_json"])
            if not isinstance(state, dict) or source["state_hash"] != _digest({"job_id": job_id, "source_key": source["source_key"], "state": state}):
                raise StateConflict("collection source progress integrity differs")
            result["sources"][source["source_key"]] = state
        return result

    def collection_observations(self, job_id: str) -> tuple[dict, ...]:
        self._job_row(job_id)
        rows = self._connection.execute("SELECT * FROM collection_observations WHERE job_id=? ORDER BY generation LIMIT 101", (job_id,)).fetchall()
        if len(rows) > 100:
            raise StateConflict("collection observation history requires pagination")
        return tuple(dict(row) for row in rows)

    def defer(self, lease: ComputeLease, state: str, error_code: str, delay_seconds: float = 0) -> Job:
        if state not in ("retry_wait", "auth_required", "capacity_blocked", "waiting_for_data", "failed"):
            raise ValueError("invalid deferred state")
        if re.fullmatch(r"[a-z][a-z0-9_]{0,79}", error_code) is None:
            raise ValueError("error code must be a safe symbolic identifier")
        _validate_duration(delay_seconds, is_zero_allowed=True)
        with _transaction(self._connection):
            row = self._require_lease(lease)
            if state == "retry_wait" and row["attempt"] >= row["max_attempts"]:
                state = "failed"
            self._connection.execute(
                "UPDATE jobs SET next_attempt_at=? WHERE job_id=?", (self._now() + delay_seconds, lease.job_id)
            )
            self._transition(lease.job_id, state, error_code, is_clear_lease=True)
            return self.get_job(lease.job_id)

    def resume(self, job_id: str) -> Job:
        with _transaction(self._connection):
            row = self._job_row(job_id)
            if row["state"] == "waiting_for_data":
                raise StateConflict("new data requires a new immutable task")
            if row["state"] not in ("auth_required", "capacity_blocked"):
                raise StateConflict("job is not resumable")
            state = "queued" if row["attempt"] < row["max_attempts"] else "failed"
            self._connection.execute("UPDATE jobs SET next_attempt_at=? WHERE job_id=?", (self._now(), job_id))
            self._transition(job_id, state, is_clear_lease=True)
            return self.get_job(job_id)

    def cancel(self, job_id: str) -> Job:
        with _transaction(self._connection):
            row = self._job_row(job_id)
            if row["state"] in ("publishing", "publish_uncertain"):
                raise StateConflict("publication must be reconciled before cancellation")
            if row["state"] in ("succeeded", "failed", "superseded", "cancelled"):
                return self.get_job(job_id)
            self._transition(job_id, "cancelled", is_clear_lease=True)
            self._connection.execute("UPDATE dispatches SET state='cancelled' WHERE job_id=? AND state='pending'", (job_id,))
            return self.get_job(job_id)

    def recover(self) -> None:
        with _transaction(self._connection):
            self._recover()

    def reroute(self, job_id: str, executor_type: str, code_sha: str) -> Job:
        if executor_type not in ("local", "vercel", "actions"):
            raise ValueError("unsupported executor")
        with _transaction(self._connection):
            row = self._job_row(job_id)
            if row["state"] not in ("queued", "retry_wait", "capacity_blocked") or row["code_sha"] != code_sha:
                raise StateConflict("cannot reroute active, terminal, or incompatible work")
            spec = replace(_job(row).spec, executor_type=executor_type)
            self._connection.execute(
                "UPDATE jobs SET executor_type=?,spec_json=?,next_attempt_at=? WHERE job_id=?",
                (executor_type, canonical_json(asdict(spec)), self._now(), job_id),
            )
            self._transition(job_id, "queued", is_clear_lease=True)
            return self.get_job(job_id)

    def begin_publication(self, job_id: str, publisher_id: str, code_sha: str,
                          resources: tuple[str, ...]) -> PublicationClaim | None:
        validate_identifier(publisher_id)
        if isinstance(resources, str):
            raise ValueError("resources must be a sequence of workbook IDs")
        resources = tuple(sorted(resources))
        if not resources or len(set(resources)) != len(resources):
            raise ValueError("publication resources must be nonempty and unique")
        for resource in resources:
            validate_identifier(resource)
        with _transaction(self._connection):
            row = self._job_row(job_id)
            if row["code_sha"] != code_sha:
                raise StateConflict("publisher version mismatch")
            if row["state"] == "publishing":
                existing = self._connection.execute(
                    "SELECT * FROM publications WHERE job_id=? AND state='publishing'", (job_id,)
                ).fetchone()
                if existing and existing["publisher_id"] == publisher_id and tuple(json.loads(existing["resources_json"])) == resources:
                    claim = _publication(existing, code_sha)
                    self._require_publication(claim)
                    return claim
                raise ResourceBusy("job already has a publisher")
            if row["state"] != "computed" or row["result_id"] is None:
                raise StateConflict("job has no publishable computation")
            if not self._is_current(row):
                self._transition(job_id, "superseded")
                return None
            self._require_no_publisher()
            publication_id = uuid.uuid4().hex
            commit_id = _digest({"publication": publication_id, "job": job_id, "result": row["result_id"]})
            self._connection.execute(
                "INSERT INTO publications VALUES(?,?,?,1,?,'publishing',?,?,?)",
                (publication_id, job_id, publisher_id, commit_id, canonical_json(resources), self._now(), self._now()),
            )
            self._connection.execute("INSERT INTO publication_safety_phases VALUES(?,0)", (publication_id,))
            self._connection.executemany(
                "INSERT INTO resource_locks VALUES(?,?)",
                ((resource, publication_id) for resource in ("global:publisher", *("sheet:" + item for item in resources))),
            )
            self._connection.executemany(
                "INSERT INTO publication_resources VALUES(?,?,'pending',NULL,?)",
                ((publication_id, resource, self._now()) for resource in resources),
            )
            self._transition(job_id, "publishing")
            return _publication(self._connection.execute(
                "SELECT * FROM publications WHERE publication_id=?", (publication_id,)
            ).fetchone(), code_sha)

    def complete_publication(self, claim: PublicationClaim, observations: Mapping[str, str | None]) -> Job:
        with _transaction(self._connection):
            row = self._require_publication(claim, is_finished_allowed=True)
            _validate_observations(claim.resources, observations)
            if any(observations[resource] != claim.commit_id for resource in claim.resources):
                raise StateConflict("not every resource has the expected commit")
            if row["state"] == "succeeded":
                return self.get_job(claim.job_id)
            for resource in claim.resources:
                self._record_observation(claim.publication_id, resource, observations[resource], claim.commit_id)
            self._finish_publication(claim.publication_id, claim.job_id)
            return self.get_job(claim.job_id)

    def mark_publication_uncertain(self, claim: PublicationClaim) -> None:
        with _transaction(self._connection):
            self._require_publication(claim)
            self._connection.execute(
                "UPDATE publications SET state='publish_uncertain',updated_at=? WHERE publication_id=?",
                (self._now(), claim.publication_id),
            )
            self._connection.execute(
                "UPDATE publication_resources SET state='unknown',updated_at=? "
                "WHERE publication_id=? AND state!='verified'", (self._now(), claim.publication_id),
            )
            self._transition(claim.job_id, "publish_uncertain", "publication_unverified")

    def record_resource_observation(self, claim: PublicationClaim, resource: str, observed_commit_id: str | None) -> None:
        if resource not in claim.resources:
            raise StateConflict("observation is outside publication resources")
        if observed_commit_id is not None and not isinstance(observed_commit_id, str):
            raise ValueError("invalid observed commit")
        with _transaction(self._connection):
            self._require_publication(claim)
            self._record_observation(claim.publication_id, resource, observed_commit_id, claim.commit_id)

    def publication_progress(self, publication_id: str) -> tuple[dict, ...]:
        return tuple(dict(row) for row in self._connection.execute(
            "SELECT resource_id,state,observed_commit_id,updated_at FROM publication_resources "
            "WHERE publication_id=? ORDER BY resource_id", (publication_id,)
        ))

    def validate_publication_claim(self, claim: PublicationClaim, is_finished_allowed: bool = False) -> None:
        self._require_publication(claim, is_finished_allowed)

    def publication_safety_metadata(self, publication_id: str) -> dict:
        row = self._connection.execute("SELECT p.*,j.code_sha,j.result_id,j.scope_id,j.state AS job_state,s.is_send_started "
            "FROM publication_effective_states p JOIN jobs j ON j.job_id=p.job_id LEFT JOIN publication_safety_phases s ON s.publication_id=p.publication_id "
            "WHERE p.publication_id=?", (publication_id,)).fetchone()
        if row is None:
            raise StateConflict("unknown publication safety scope")
        return {**dict(row),"resources":tuple(json.loads(row["resources_json"])),"has_safety_phase":row["is_send_started"] is not None}

    def publication_base(self, publication_id: str, resource: str) -> Document | None:
        row = self._connection.execute("SELECT document_id FROM publication_bases WHERE publication_id=? AND resource_id=?", (publication_id,resource)).fetchone()
        return self.get_document(row[0]) if row else None

    def record_publication_base(self, claim: PublicationClaim, resource: str, document: Document) -> str:
        from application.publication_safety import read_publication_state
        data = read_publication_state(document)
        with _transaction(self._connection):
            self._require_publication(claim)
            job = self.get_job(claim.job_id)
            if (resource not in claim.resources or data["resource"]!=resource or data["publication_id"]!=claim.publication_id
                    or data["commit_id"]!=claim.commit_id or data["result_id"]!=job.result_id or data["code_sha"]!=claim.code_sha):
                raise StateConflict("publication baseline source differs")
            existing = self.publication_base(claim.publication_id,resource)
            if existing is not None:
                if existing!=document:
                    raise StateConflict("publication baseline cannot be overwritten")
                return existing.snapshot_id
            phase = self._connection.execute("SELECT is_send_started FROM publication_safety_phases WHERE publication_id=?", (claim.publication_id,)).fetchone()
            if phase is None or phase[0]!=0:
                raise StateConflict("original baseline must precede every send")
            self._put_object(document.serialized)
            self._connection.execute("INSERT INTO publication_bases VALUES(?,?,?)", (claim.publication_id,resource,document.snapshot_id))
            return document.snapshot_id

    def mark_publication_send_started(self, claim: PublicationClaim) -> None:
        with _transaction(self._connection):
            self._require_publication(claim)
            self._connection.execute("UPDATE publication_safety_phases SET is_send_started=1 WHERE publication_id=?", (claim.publication_id,))

    def recover_publication(self, publication_id: str, new_publisher_id: str, observations: Mapping[str, str | None],
                            is_writer_stopped: bool) -> PublicationClaim | None:
        """Only a trusted reconciler may supply writer termination/read-back evidence."""
        validate_identifier(new_publisher_id)
        if is_writer_stopped is not True:
            raise StateConflict("old writer termination must be established")
        with _transaction(self._connection):
            row = self._connection.execute(
                "SELECT * FROM publications WHERE publication_id=?", (publication_id,)
            ).fetchone()
            if (row is None or row["state"] != "publish_uncertain" or self._connection.execute(
                    "SELECT 1 FROM publication_abort_receipts WHERE publication_id=?", (publication_id,)).fetchone()):
                raise StateConflict("publication is not awaiting reconciliation")
            resources = tuple(json.loads(row["resources_json"]))
            _validate_observations(resources, observations)
            self._require_publication_locks(publication_id, resources)
            for resource in resources:
                self._record_observation(publication_id, resource, observations[resource], row["commit_id"])
            if all(observations[resource] == row["commit_id"] for resource in resources):
                self._finish_publication(publication_id, row["job_id"])
                return None
            self._connection.execute(
                "UPDATE publications SET publisher_id=?,generation=generation+1,state='publishing',updated_at=? "
                "WHERE publication_id=?", (new_publisher_id, self._now(), publication_id),
            )
            self._transition(row["job_id"], "publishing")
            updated = self._connection.execute(
                "SELECT * FROM publications WHERE publication_id=?", (publication_id,)
            ).fetchone()
            return _publication(updated, self._job_row(row["job_id"])["code_sha"])

    def set_budget(self, platform: str, period: str, limit_units: int) -> None:
        validate_identifier(platform)
        validate_identifier(period)
        if type(limit_units) is not int or limit_units < 0:
            raise ValueError("budget must be a nonnegative integer")
        with _transaction(self._connection):
            self._connection.execute(
                "INSERT INTO usage_budget(platform,period,limit_units) VALUES(?,?,?) "
                "ON CONFLICT(platform,period) DO UPDATE SET limit_units=excluded.limit_units",
                (platform, period, limit_units),
            )

    def budget_status(self, platform: str, period: str) -> dict:
        row = self._connection.execute(
            "SELECT * FROM usage_budget WHERE platform=? AND period=?", (platform, period)
        ).fetchone()
        if row is None:
            raise KeyError("unknown budget")
        return dict(row)

    def reserve_budget(self, reservation_id: str, need: BudgetNeed) -> dict:
        if not isinstance(need, BudgetNeed):
            raise ValueError("invalid budget request")
        with _transaction(self._connection):
            return self._reserve(reservation_id, need, None)

    def settle_budget(self, reservation_id: str, actual_units: int) -> None:
        if type(actual_units) is not int or actual_units < 0:
            raise ValueError("actual usage must be a nonnegative integer")
        with _transaction(self._connection):
            reservation = self._connection.execute(
                "SELECT * FROM budget_reservations WHERE reservation_id=?", (reservation_id,)
            ).fetchone()
            if reservation is None:
                raise KeyError("unknown reservation")
            if reservation["actual_units"] is not None:
                if reservation["actual_units"] != actual_units:
                    raise StateConflict("usage settlement changed")
                return
            self._connection.execute(
                "UPDATE usage_budget SET reserved_units=reserved_units-?,used_units=used_units+? WHERE platform=? AND period=?",
                (reservation["units"], actual_units, reservation["platform"], reservation["period"]),
            )
            self._connection.execute(
                "UPDATE budget_reservations SET actual_units=? WHERE reservation_id=?", (actual_units, reservation_id)
            )

    def reservations(self, job_id: str) -> tuple[dict, ...]:
        self._job_row(job_id)
        return tuple(dict(row) for row in self._connection.execute(
            "SELECT * FROM budget_reservations WHERE job_id=? ORDER BY reservation_id", (job_id,)
        ))

    def dispatch_reservations(self, dispatch_id: str) -> tuple[dict, ...]:
        if self._connection.execute("SELECT 1 FROM dispatches WHERE dispatch_id=?", (dispatch_id,)).fetchone() is None:
            raise KeyError("unknown dispatch")
        return tuple(dict(row) for row in self._connection.execute(
            "SELECT b.* FROM budget_reservations b JOIN dispatch_budgets d ON d.reservation_id=b.reservation_id "
            "WHERE d.dispatch_id=? ORDER BY b.platform", (dispatch_id,)
        ))

    def schedule_slot(self, schedule_id: str, slot_utc: int, spec: TaskSpec) -> Job:
        validate_identifier(schedule_id)
        if type(slot_utc) is not int or slot_utc < 0:
            raise ValueError("schedule slot must be a nonnegative UTC epoch second")
        slot_id = _digest({"schedule": schedule_id, "slot_utc": slot_utc})
        bound = replace(spec, intent_id=slot_id)
        with _transaction(self._connection):
            existing = self._connection.execute(
                "SELECT * FROM schedule_slots WHERE schedule_id=? AND slot_utc=?", (schedule_id, slot_utc)
            ).fetchone()
            if existing:
                if existing["signature"] != bound.request_signature:
                    raise StateConflict("schedule slot reused with different inputs")
                return self.get_job(existing["job_id"])
            job = self._enqueue("schedule:" + slot_id, bound, bound.command in AUTO_COMMANDS)
            self._connection.execute(
                "INSERT INTO schedule_slots VALUES(?,?,?,?)", (schedule_id, slot_utc, job.job_id, bound.request_signature)
            )
            return job

    def _enqueue(self, request_id: str, spec: TaskSpec, is_desired: bool) -> Job:
        validate_identifier(request_id)
        if not isinstance(spec, TaskSpec) or not isinstance(is_desired, bool):
            raise ValueError("invalid task submission")
        signature = _digest({"spec": asdict(spec), "is_desired": is_desired})
        replay = self._connection.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
        if replay:
            if replay["signature"] != signature:
                raise StateConflict("request ID reused with different content")
            return self.get_job(replay["job_id"])
        if not self._is_union_input_current(spec) or not self._is_raid_source_current(spec) or not self._is_system_status_current(spec):
            raise StateConflict("task source head is stale, missing, or inactive")
        config_id = spec.version_map.get("healthcheck_config")
        if config_id is not None and self._connection.execute(
            "SELECT 1 FROM config_versions WHERE config_id=?", (config_id,)
        ).fetchone() is None:
            raise StateConflict("task configuration is not a published version")
        is_changed = False
        if is_desired:
            if spec.command not in AUTO_COMMANDS:
                raise ValueError("manual actions cannot supersede other work")
            desired = self._connection.execute(
                "SELECT fingerprint FROM desired_targets WHERE scope_id=? AND family=?", (spec.scope_id, spec.family)
            ).fetchone()
            if desired is None or desired["fingerprint"] != spec.fingerprint:
                is_changed = True
                self._require_no_publisher()
                self._connection.execute(
                    "INSERT INTO desired_targets VALUES(?,?,?) ON CONFLICT(scope_id,family) "
                    "DO UPDATE SET fingerprint=excluded.fingerprint", (spec.scope_id, spec.family, spec.fingerprint),
                )
        existing = self._connection.execute("SELECT * FROM jobs WHERE fingerprint=?", (spec.fingerprint,)).fetchone()
        if existing:
            job_id = existing["job_id"]
            if is_changed and existing["state"] in ("succeeded", "superseded"):
                state = "computed" if existing["result_id"] is not None else "queued"
                self._transition(job_id, state, is_clear_lease=True)
        else:
            for dependency in spec.dependencies:
                self._job_row(dependency)
            job_id = spec.fingerprint
            self._connection.execute(
                "INSERT INTO jobs(job_id,fingerprint,spec_json,command,scope_id,family,code_sha,executor_type,priority,"
                "max_attempts,state,next_attempt_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,'queued',?,?,?)",
                (job_id, spec.fingerprint, canonical_json(asdict(spec)), spec.command, spec.scope_id, spec.family,
                 spec.code_sha, spec.executor_type, spec.priority, spec.max_attempts, self._now(), self._now(), self._now()),
            )
            self._connection.executemany(
                "INSERT INTO job_dependencies VALUES(?,?)", ((job_id, dependency) for dependency in spec.dependencies)
            )
            self._event(job_id, "queued")
        self._connection.execute("INSERT INTO requests VALUES(?,?,?,?)", (request_id, signature, job_id, self._now()))
        return self.get_job(job_id)

    def _config_publication(self, candidate: sqlite3.Row) -> ConfigPublication:
        jobs = self._connection.execute(
            "SELECT job_id FROM candidate_jobs WHERE candidate_id=? ORDER BY job_id", (candidate["candidate_id"],)
        ).fetchall()
        return ConfigPublication(candidate["candidate_id"], candidate["document_id"], tuple(row["job_id"] for row in jobs))

    def _put_object(self, serialized: str) -> str:
        object_id = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        existing = self._connection.execute("SELECT serialized FROM objects WHERE object_id=?", (object_id,)).fetchone()
        if existing is not None and existing["serialized"] != serialized:
            raise StateConflict("immutable object collision")
        self._connection.execute("INSERT OR IGNORE INTO objects VALUES(?,?,?)", (object_id, serialized, self._now()))
        return object_id

    def _get_serialized(self, object_id: str) -> str:
        row = self._connection.execute("SELECT serialized FROM objects WHERE object_id=?", (object_id,)).fetchone()
        if row is None:
            raise KeyError("unknown object")
        if hashlib.sha256(row["serialized"].encode("utf-8")).hexdigest() != object_id:
            raise StateConflict("persisted object failed integrity verification")
        return row["serialized"]

    def _job_row(self, job_id: str) -> sqlite3.Row:
        row = self._connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError("unknown job")
        return row

    def _require_lease(self, lease: ComputeLease) -> sqlite3.Row:
        row = self._job_row(lease.job_id)
        if (row["state"] != "running" or row["owner"] != lease.worker_id or row["generation"] != lease.generation
                or row["code_sha"] != lease.code_sha or row["expires_at"] <= self._now()):
            raise LeaseLost("computation lease is stale or expired")
        return row

    def _is_current(self, row: sqlite3.Row) -> bool:
        spec = _job(row).spec
        if not self._is_union_input_current(spec) or not self._is_raid_source_current(spec) or not self._is_system_status_current(spec):
            return False
        if row["command"] not in AUTO_COMMANDS:
            return True
        config_id = _job(row).spec.version_map.get("healthcheck_config")
        if config_id is not None:
            current = self._connection.execute(
                "SELECT h.config_id FROM config_versions v JOIN config_heads h "
                "ON h.config_type=v.config_type AND h.scope_id=v.scope_id WHERE v.config_id=?", (config_id,)
            ).fetchone()
            if current is None or current["config_id"] != config_id:
                return False
        desired = self._connection.execute(
            "SELECT fingerprint FROM desired_targets WHERE scope_id=? AND family=?", (row["scope_id"], row["family"])
        ).fetchone()
        return desired is None or desired["fingerprint"] == row["fingerprint"]

    def _is_union_input_current(self, spec: TaskSpec) -> bool:
        head = self.union_input_version(spec.scope_id)
        if head is None:
            return True
        bound = spec.version_map.get("union_inputs")
        if bound is None and spec.command not in ("recompute_healthcheck", "generate_raid_draft", "publish_raid_plan"):
            return True
        return bound == head["input_id"] and head["roster_state"] == "active"

    def _is_system_status_current(self, spec: TaskSpec) -> bool:
        if spec.command != "publish_system_status":
            return True
        from application.system_status_publication import SCOPE, read_system_status_route
        if spec.scope_id != SCOPE:
            return False
        if self.union_input_version(SCOPE) is not None:
            return False
        head = self._connection.execute("SELECT route_id FROM system_status_route_heads WHERE scope_id=?", (SCOPE,)).fetchone()
        active = self._connection.execute("SELECT occurrence_id FROM system_status_observation_heads WHERE scope_id=?", (SCOPE,)).fetchone()
        if head is None or active is None or spec.version_map.get("status_route") != head[0] or spec.version_map.get("status_occurrence") != active[0]:
            return False
        try:
            route = read_system_status_route(self.get_document(head[0]))
            return route["is_enabled"] and route["code_sha"] == spec.code_sha
        except (StateConflict, ValueError, KeyError, TypeError):
            return False

    def _is_raid_source_current(self, spec: TaskSpec) -> bool:
        if spec.command not in ("generate_raid_draft", "publish_raid_plan"):
            return True
        versions = spec.version_map
        if (spec.command == "generate_raid_draft" and self.union_input_version(spec.scope_id) is None
                and self.raid_source_version(spec.scope_id) is None and "raid_compiler" not in versions):
            return True
        from application.raid_codec import restore_problem
        from application.raid_inputs import COMPILER, is_raid_dependency_current
        if versions.get("raid_compiler") != COMPILER or "problem" not in versions:
            return False
        problem = restore_problem(self.get_result(versions["problem"]))
        if not (problem.problem_id == versions["problem"] and problem.code_sha == spec.code_sha and all(versions.get(key) == value for key, value in problem.input_versions)
                and is_raid_dependency_current(self, spec.scope_id, problem)):
            return False
        if spec.command == "generate_raid_draft":
            if "raid_source_bundle" in versions:
                source_head = self.raid_source_version(spec.scope_id)
                if source_head is None or versions['raid_source_bundle'] != source_head['bundle_id']:
                    return False
            return True
        source_head = self.raid_source_version(spec.scope_id)
        if source_head is not None and versions.get("raid_route") != source_head["route_id"]:
            return False
        confirmation_id = versions.get("raid_confirmation")
        row = self._connection.execute("SELECT c.signature,d.problem_id,i.problem_id AS current_problem FROM raid_confirmed_heads h "
            "JOIN raid_confirmations c ON c.confirmation_id=h.confirmation_id JOIN raid_drafts d ON d.scope_id=c.scope_id AND d.draft_id=c.draft_id "
            "JOIN raid_input_heads i ON i.scope_id=h.scope_id WHERE h.scope_id=? AND h.confirmation_id=?", (spec.scope_id, confirmation_id)).fetchone()
        return row is not None and hashlib.sha256(row["signature"].encode()).hexdigest() == confirmation_id and row["problem_id"] == row["current_problem"] == problem.problem_id

    def _require_no_publisher(self) -> None:
        if (self._connection.execute("SELECT 1 FROM resource_locks LIMIT 1").fetchone()
                or self._connection.execute("SELECT 1 FROM publication_effective_states WHERE effective_state IN ('publishing','publish_uncertain') LIMIT 1").fetchone()):
            raise ResourceBusy("a publication must be resolved before promoting desired versions")

    def _require_publication(self, claim: PublicationClaim, is_finished_allowed: bool = False) -> sqlite3.Row:
        row = self._connection.execute(
            "SELECT * FROM publications WHERE publication_id=?", (claim.publication_id,)
        ).fetchone()
        job = self._job_row(claim.job_id)
        states = ("publishing", "succeeded") if is_finished_allowed else ("publishing",)
        if (row is None or row["state"] not in states or (row["state"] == "publishing" and job["state"] != "publishing")
                or row["job_id"] != claim.job_id or row["publisher_id"] != claim.publisher_id
                or row["generation"] != claim.generation or row["commit_id"] != claim.commit_id
                or job["code_sha"] != claim.code_sha or tuple(json.loads(row["resources_json"])) != claim.resources):
            raise LeaseLost("publication claim is stale")
        if row["state"] == "publishing":
            self._require_publication_locks(claim.publication_id, claim.resources)
        return row

    def _require_publication_locks(self, publication_id: str, resources: tuple[str, ...]) -> None:
        held = {row["resource_id"] for row in self._connection.execute(
            "SELECT resource_id FROM resource_locks WHERE publication_id=?", (publication_id,)
        )}
        expected = {"global:publisher", *("sheet:" + resource for resource in resources)}
        if held != expected:
            raise StateConflict("publication lock integrity failed")
        recorded = {row["resource_id"] for row in self._connection.execute(
            "SELECT resource_id FROM publication_resources WHERE publication_id=?", (publication_id,)
        )}
        if recorded != set(resources):
            raise StateConflict("publication resource manifest integrity failed")

    def _record_observation(self, publication_id: str, resource: str, observed: str | None, expected: str) -> None:
        self._connection.execute(
            "UPDATE publication_resources SET state=?,observed_commit_id=?,updated_at=? "
            "WHERE publication_id=? AND resource_id=?",
            ("verified" if observed == expected else "unknown", observed, self._now(), publication_id, resource),
        )

    def _finish_publication(self, publication_id: str, job_id: str) -> None:
        self._connection.execute(
            "UPDATE publications SET state='succeeded',updated_at=? WHERE publication_id=?", (self._now(), publication_id)
        )
        self._transition(job_id, "succeeded", is_clear_lease=True)
        self._connection.execute("DELETE FROM resource_locks WHERE publication_id=?", (publication_id,))

    def _reserve(self, reservation_id: str, need: BudgetNeed, job_id: str | None) -> dict:
        validate_identifier(reservation_id)
        existing = self._connection.execute(
            "SELECT * FROM budget_reservations WHERE reservation_id=?", (reservation_id,)
        ).fetchone()
        if existing:
            if (existing["platform"], existing["period"], existing["units"], existing["job_id"]) != (
                need.platform, need.period, need.units, job_id
            ):
                raise StateConflict("reservation ID reused with different content")
            return dict(existing)
        budget = self._connection.execute(
            "SELECT * FROM usage_budget WHERE platform=? AND period=?", (need.platform, need.period)
        ).fetchone()
        if budget is None or budget["used_units"] + budget["reserved_units"] + need.units > budget["limit_units"]:
            raise BudgetExceeded("free allowance cannot cover this reservation")
        self._connection.execute(
            "UPDATE usage_budget SET reserved_units=reserved_units+? WHERE platform=? AND period=?",
            (need.units, need.platform, need.period),
        )
        self._connection.execute(
            "INSERT INTO budget_reservations(reservation_id,platform,period,units,job_id) VALUES(?,?,?,?,?)",
            (reservation_id, need.platform, need.period, need.units, job_id),
        )
        return dict(self._connection.execute(
            "SELECT * FROM budget_reservations WHERE reservation_id=?", (reservation_id,)
        ).fetchone())

    def _reserve_claim_budget(self, job_id: str, generation: int | str, needs: tuple[BudgetNeed, ...]) -> tuple[str, ...]:
        # BEGIN IMMEDIATE already holds the write lock. Preflight all resources
        # before reserving any, so one exhausted resource cannot leak another.
        for need in needs:
            row = self._connection.execute(
                "SELECT * FROM usage_budget WHERE platform=? AND period=?", (need.platform, need.period)
            ).fetchone()
            if row is None or row["used_units"] + row["reserved_units"] + need.units > row["limit_units"]:
                raise BudgetExceeded("free allowance cannot cover this execution")
        reservation_ids = []
        for need in needs:
            reservation_id = _digest({"job": job_id, "generation": generation, "budget": asdict(need)})
            self._reserve(reservation_id, need, job_id)
            reservation_ids.append(reservation_id)
        return tuple(reservation_ids)

    def _recover(self) -> None:
        for dispatch in self._connection.execute(
            "SELECT * FROM dispatches WHERE state='pending' AND expires_at<=?", (self._now(),)
        ).fetchall():
            self._connection.execute("UPDATE dispatches SET state='expired' WHERE dispatch_id=?", (dispatch["dispatch_id"],))
            row = self._job_row(dispatch["job_id"])
            if row["state"] == "dispatched":
                count = self._connection.execute("SELECT COUNT(*) FROM dispatches WHERE job_id=?", (row["job_id"],)).fetchone()[0]
                state = "retry_wait" if count < row["max_attempts"] else "failed"
                self._transition(row["job_id"], state, "dispatch_expired")
        for row in self._connection.execute("SELECT * FROM jobs WHERE state='running' AND expires_at<=?", (self._now(),)).fetchall():
            state = "retry_wait" if row["attempt"] < row["max_attempts"] else "failed"
            self._transition(row["job_id"], state, "lease_expired", is_clear_lease=True)
        while True:
            blocked = self._connection.execute(
                "SELECT j.job_id FROM jobs j WHERE j.state IN ('queued','retry_wait','waiting_for_data','capacity_blocked','auth_required') "
                "AND EXISTS(SELECT 1 FROM job_dependencies d JOIN jobs p ON p.job_id=d.parent_id "
                "WHERE d.job_id=j.job_id AND p.state IN ('failed','cancelled','superseded'))"
            ).fetchall()
            if not blocked:
                break
            for row in blocked:
                self._transition(row["job_id"], "failed", "dependency_failed", is_clear_lease=True)

    def _transition(self, job_id: str, state: str, error_code: str | None = None, is_clear_lease: bool = False) -> None:
        self._connection.execute(
            "UPDATE jobs SET state=?,error_code=?,updated_at=? WHERE job_id=?", (state, error_code, self._now(), job_id)
        )
        if is_clear_lease:
            self._connection.execute("UPDATE jobs SET owner=NULL,expires_at=NULL WHERE job_id=?", (job_id,))
        self._event(job_id, state, error_code)

    def _event(self, job_id: str, state: str, error_code: str | None = None) -> None:
        self._connection.execute(
            "INSERT INTO job_events(job_id,state,error_code,created_at) VALUES(?,?,?,?)", (job_id, state, error_code, self._now())
        )

    def _now(self) -> float:
        value = self._clock()
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError("clock must return a finite nonnegative epoch time")
        return float(value)

    def _claim_dispatch(self, dispatch_id: str, worker_id: str, code_sha: str, executor_type: str,
                        executor_run_id: str, lease_seconds: float) -> ComputeLease | None:
        validate_identifier(dispatch_id)
        validate_identifier(executor_run_id)
        dispatch = self._connection.execute("SELECT * FROM dispatches WHERE dispatch_id=?", (dispatch_id,)).fetchone()
        if dispatch is None or dispatch["code_sha"] != code_sha or dispatch["executor_type"] != executor_type:
            raise LeaseLost("dispatch identity or executor version mismatch")
        row = self._job_row(dispatch["job_id"])
        if dispatch["state"] == "acknowledged":
            lease = ComputeLease(row["job_id"], worker_id, code_sha, dispatch["lease_generation"])
            if dispatch["executor_run_id"] != executor_run_id or dispatch["worker_id"] != worker_id:
                raise LeaseLost("dispatch already belongs to another executor run")
            self._require_lease(lease)
            return lease
        if (dispatch["state"] != "pending" or row["state"] != "dispatched" or dispatch["expires_at"] <= self._now()
                or row["attempt"] >= row["max_attempts"]):
            raise LeaseLost("dispatch is no longer claimable")
        if not self._is_current(row):
            self._connection.execute("UPDATE dispatches SET state='cancelled' WHERE dispatch_id=?", (dispatch_id,))
            self._transition(row["job_id"], "superseded")
            return None
        self._connection.execute(
            "UPDATE jobs SET generation=generation+1,owner=?,expires_at=?,attempt=attempt+1 WHERE job_id=?",
            (worker_id, self._now() + lease_seconds, row["job_id"]),
        )
        self._connection.execute(
            "UPDATE dispatches SET state='acknowledged',executor_run_id=?,worker_id=?,lease_generation=? WHERE dispatch_id=?",
            (executor_run_id, worker_id, row["generation"] + 1, dispatch_id),
        )
        self._transition(row["job_id"], "running")
        return ComputeLease(row["job_id"], worker_id, code_sha, row["generation"] + 1)


def _candidate_semantic(document: Document, base_config_id: str | None) -> str:
    return _digest({"payload": document.payload, "reference": document.envelope["reference_version"],
                    "schema": document.envelope["schema_version"], "scope": document.envelope["scope_id"], "base": base_config_id})


def _job(row: sqlite3.Row) -> Job:
    return Job(row["job_id"], TaskSpec(**json.loads(row["spec_json"])), row["state"], row["attempt"], row["generation"],
               row["owner"], row["expires_at"], row["result_id"], row["error_code"], row["next_attempt_at"])


def _publication(row: sqlite3.Row, code_sha: str) -> PublicationClaim:
    return PublicationClaim(row["publication_id"], row["job_id"], row["publisher_id"], row["generation"], code_sha,
                            row["commit_id"], tuple(json.loads(row["resources_json"])))


def _dispatch(row: sqlite3.Row) -> Dispatch:
    return Dispatch(row["dispatch_id"], row["job_id"], row["executor_type"], row["code_sha"], row["expires_at"])


def _budget_needs(executor_type: str, budget: BudgetNeed | tuple[BudgetNeed, ...] | None) -> tuple[BudgetNeed, ...]:
    if executor_type not in ("local", "actions", "vercel"):
        raise ValueError("unsupported executor")
    needs = (budget,) if isinstance(budget, BudgetNeed) else tuple(budget or ())
    if any(not isinstance(need, BudgetNeed) for need in needs):
        raise ValueError("invalid budget request")
    platforms = {need.platform for need in needs}
    if len(platforms) != len(needs):
        raise ValueError("duplicate budget platform")
    if executor_type == "actions" and platforms != {"actions_minutes"}:
        raise ValueError("Actions requires its minute budget")
    if executor_type == "vercel" and platforms != {"vercel_cpu_ms", "vercel_memory_mb_seconds"}:
        raise ValueError("Vercel requires both CPU and memory budgets")
    return tuple(sorted(needs, key=lambda need: need.platform))


def _validate_observations(resources: tuple[str, ...], observations: Mapping[str, str | None]) -> None:
    if set(observations) != set(resources):
        raise StateConflict("read-back evidence must cover exactly the publication resources")
    if any(value is not None and not isinstance(value, str) for value in observations.values()):
        raise ValueError("invalid observed commit")


def _digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _validate_duration(value: float, is_zero_allowed: bool = False) -> None:
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            or value < 0 or (value == 0 and not is_zero_allowed) or value > 86400):
        raise ValueError("invalid bounded duration")


@contextmanager
def _transaction(connection: sqlite3.Connection):
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def _run_migration(connection: sqlite3.Connection, path: Path, now: float) -> None:
    version = int(path.name.split("_")[0])
    source = path.read_text(encoding="utf-8")
    checksum = hashlib.sha256(source.encode("utf-8")).hexdigest()
    with _transaction(connection):
        existing = connection.execute("SELECT checksum FROM schema_migrations WHERE version=?", (version,)).fetchone()
        if existing:
            if existing["checksum"] != checksum:
                raise StateConflict("applied migration checksum changed")
            return
        statement = ""
        for line in source.splitlines(keepends=True):
            statement += line
            if sqlite3.complete_statement(statement):
                connection.execute(statement)
                statement = ""
        if statement.strip():
            raise ValueError("incomplete migration SQL")
        connection.execute("INSERT INTO schema_migrations VALUES(?,?,?)", (version, checksum, now))
