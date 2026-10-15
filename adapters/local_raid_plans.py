"""Local mock draft/confirmation persistence, sharing the coordinator migration set."""

from dataclasses import asdict
import hashlib
import json

from adapters.local_state import LocalStateStore, _transaction
from application.tasks import StateConflict, validate_identifier
from application.raid_codec import restore_problem
from application.raid_execution import restore_final
from application.raid_workbook import render_raid_workbook
from application.raid_inputs import compile_raid_problem, is_raid_dependency_current
from application.raid_publication import compile_raid_publication, raid_publication_task
from domain.raid_finalize import FinalRaidPlan
from domain.raid_planner import RaidProblem
from domain.raid_view import build_raid_view
from domain.snapshots import canonical_json


class LocalRaidPlans(LocalStateStore):
    def prepare_raid_publication(self, request_id, candidate, confirmed_candidate_id, intent_id):
        if confirmed_candidate_id != candidate.candidate_id:
            raise StateConflict("raid publication confirmation differs")
        with _transaction(self._connection):
            spec = raid_publication_task(candidate, intent_id)
            request = self._connection.execute("SELECT job_id FROM requests WHERE request_id=?", (request_id,)).fetchone()
            if request is not None:
                # The normal request signature still verifies exact replay after sources change.
                return self._enqueue(request_id, spec, False)
            rebuilt = compile_raid_publication(self, candidate.confirmation_id, candidate.route_id, candidate.code_sha)
            if rebuilt != candidate or confirmed_candidate_id != candidate.candidate_id:
                raise StateConflict("raid publication confirmation differs")
            self._require_no_publisher()
            self._put_object(canonical_json(asdict(candidate)))
            return self._enqueue(request_id, spec, False)

    def observe_problem(self, scope_id: str, problem: RaidProblem, expected_revision: int) -> int:
        validate_identifier(scope_id)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("nonnegative input revision required")
        with _transaction(self._connection):
            return self._observe_problem(scope_id, problem, expected_revision)

    def input_head(self, scope_id: str) -> dict | None:
        row = self._connection.execute("SELECT * FROM raid_input_heads WHERE scope_id=?", (scope_id,)).fetchone()
        return dict(row) if row else None

    def save_draft(self, scope_id: str, problem: RaidProblem, final: FinalRaidPlan) -> str:
        validate_identifier(scope_id)
        view = build_raid_view(problem, final)
        with _transaction(self._connection):
            problem_id = self._put_object(canonical_json(asdict(problem)))
            final_id = self._put_object(canonical_json(asdict(final)))
            view_id = self._put_object(canonical_json(asdict(view)))
            self._connection.execute("INSERT OR IGNORE INTO raid_drafts VALUES(?,?,?,?,?)",
                                     (final.draft_plan_id, scope_id, problem_id, final_id, view_id))
            existing = self.draft(scope_id, final.draft_plan_id)
            if existing["problem_id"] != problem_id or existing["final_id"] != final_id or existing["view_id"] != view_id:
                raise StateConflict("draft identity cannot change")
        return final.draft_plan_id

    def draft(self, scope_id: str, draft_id: str) -> dict:
        row = self._connection.execute("SELECT * FROM raid_drafts WHERE scope_id=? AND draft_id=?", (scope_id, draft_id)).fetchone()
        if not row:
            raise StateConflict("unknown scoped raid draft")
        result = dict(row)
        for field in ("problem_id", "final_id", "view_id"):
            self.get_result(result[field])  # content hash verification on every read
        if result["final_id"] != draft_id:
            raise StateConflict("draft payload identity differs")
        return result

    def confirmed_head(self, scope_id: str) -> str | None:
        row = self._connection.execute("SELECT confirmation_id FROM raid_confirmed_heads WHERE scope_id=?", (scope_id,)).fetchone()
        return row[0] if row else None

    def confirm(self, request_id: str, intent_id: str, scope_id: str, draft_id: str, actor_id: str,
                expected_input_revision: int, expected_confirmed_id: str | None,
                is_accept_warnings: bool = False) -> str:
        for value in (request_id, intent_id, scope_id, draft_id, actor_id):
            validate_identifier(value)
        if type(is_accept_warnings) is not bool or type(expected_input_revision) is not int:
            raise ValueError("explicit warning decision and input revision required")
        body = {"scope_id": scope_id, "draft_id": draft_id, "intent_id": intent_id, "actor_id": actor_id,
                "input_revision": expected_input_revision, "previous_confirmation": expected_confirmed_id,
                "is_accept_warnings": is_accept_warnings}
        signature = canonical_json(body)
        confirmation_id = hashlib.sha256(signature.encode()).hexdigest()
        with _transaction(self._connection):
            request = self._connection.execute("SELECT confirmation_id FROM raid_confirmation_requests WHERE request_id=?", (request_id,)).fetchone()
            if request and request[0] != confirmation_id:
                raise StateConflict("request cannot replace confirmed raid intent")
            prior = self._connection.execute("SELECT * FROM raid_confirmations WHERE scope_id=? AND intent_id=?", (scope_id, intent_id)).fetchone()
            if prior:
                if prior["signature"] != signature:
                    raise StateConflict("intent already confirmed another immutable request")
                self.confirmation(confirmation_id)
            else:
                draft = self.draft(scope_id, draft_id)
                current = self.input_head(scope_id)
                if not current or current["revision"] != expected_input_revision or current["problem_id"] != draft["problem_id"]:
                    raise StateConflict("raid draft dependencies are stale")
                if not is_raid_dependency_current(self, scope_id, restore_problem(self.get_result(draft["problem_id"]))):
                    raise StateConflict("raid source dependencies are stale")
                if self.confirmed_head(scope_id) != expected_confirmed_id:
                    raise StateConflict("another raid confirmation changed the head")
                final = self.get_result(draft["final_id"])
                if final["warnings"] and not is_accept_warnings:
                    raise StateConflict("raid warnings require explicit acknowledgement")
                self._require_no_publisher()
                self._connection.execute("INSERT INTO raid_confirmations VALUES(?,?,?,?,?,?)",
                    (confirmation_id, scope_id, intent_id, signature, draft_id, self._now()))
                self._connection.execute("INSERT INTO raid_confirmed_heads VALUES(?,?) ON CONFLICT(scope_id) DO UPDATE SET confirmation_id=excluded.confirmation_id",
                                         (scope_id, confirmation_id))
            self._connection.execute("INSERT OR IGNORE INTO raid_confirmation_requests VALUES(?,?)", (request_id, confirmation_id))
        return confirmation_id

    def confirmation(self, confirmation_id: str) -> dict:
        row = self._connection.execute("SELECT * FROM raid_confirmations WHERE confirmation_id=?", (confirmation_id,)).fetchone()
        if not row or hashlib.sha256(row["signature"].encode()).hexdigest() != confirmation_id:
            raise StateConflict("unknown or corrupt raid confirmation")
        result = json.loads(row["signature"])
        draft = self.draft(result["scope_id"], result["draft_id"])
        return {**result, "confirmation_id": confirmation_id, "created_at": row["created_at"],
                "view": self.get_result(draft["view_id"])}

    def confirmation_status(self, confirmation_id: str) -> dict:
        record = self.confirmation(confirmation_id)
        draft = self.draft(record["scope_id"], record["draft_id"])
        current = self.input_head(record["scope_id"])
        return {"confirmation_id": confirmation_id, "is_stale": current is None or current["problem_id"] != draft["problem_id"] or not is_raid_dependency_current(self, record["scope_id"], restore_problem(self.get_result(draft["problem_id"]))),
                "is_current_confirmation": self.confirmed_head(record["scope_id"]) == confirmation_id}

    def confirmation_workbook(self, confirmation_id: str) -> dict:
        record = self.confirmation(confirmation_id)
        draft = self.draft(record["scope_id"], record["draft_id"])
        workbook = render_raid_workbook(restore_problem(self.get_result(draft["problem_id"])),
                                        restore_final(self.get_result(draft["final_id"])))
        if workbook.raid_view_id != hashlib.sha256(canonical_json(record["view"]).encode()).hexdigest():
            raise StateConflict("confirmed workbook view source differs")
        return {**self.confirmation_status(confirmation_id), "workbook_view": asdict(workbook), "workbook_view_id": workbook.view_id}

    def _observe_problem(self, scope_id, problem, expected_revision):
        if not is_raid_dependency_current(self, scope_id, problem):
            raise StateConflict("raid source dependencies are stale")
        versions = dict(problem.input_versions)
        if "raid_compiler" in versions:
            rebuilt = compile_raid_problem(self, versions["healthcheck_job"], versions["raid_settings"], versions["raid_member_inputs"], problem.code_sha)
            if rebuilt != problem:
                raise StateConflict("compiled raid problem differs from source artifacts")
        head = self.input_head(scope_id)
        revision = head["revision"] if head else 0
        if revision != expected_revision:
            raise StateConflict("raid input revision changed")
        if head and head["problem_id"] == problem.problem_id:
            return revision
        self._require_no_publisher()
        object_id = self._put_object(canonical_json(asdict(problem)))
        self._connection.execute("INSERT INTO raid_input_heads VALUES(?,?,?) ON CONFLICT(scope_id) DO UPDATE SET problem_id=excluded.problem_id,revision=excluded.revision",
                                 (scope_id, object_id, revision + 1))
        return revision + 1
