"""Immutable task contracts shared by local executors and persistence ports."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Mapping

from domain.snapshots import canonical_json


COMMANDS = frozenset({
    "prepare_config", "publish_config", "refresh_roster_and_training", "refresh_raid_history",
    "publish_union_inputs", "recompute_healthcheck", "prepare_layout", "apply_layout",
    "generate_raid_draft", "confirm_raid_plan", "send_reports", "reconcile",
})
AUTO_COMMANDS = frozenset({"recompute_healthcheck", "generate_raid_draft", "prepare_layout", "reconcile"})
INTENT_COMMANDS = frozenset({
    "prepare_config", "refresh_roster_and_training", "refresh_raid_history",
    "publish_union_inputs", "send_reports", "reconcile",
})


class StateConflict(RuntimeError):
    """A replay, version, or immutable artifact disagrees with persisted state."""


class LeaseLost(RuntimeError):
    """The current process no longer has authority to complete this operation."""


class ResourceBusy(RuntimeError):
    """A publisher owns resources; it must finish or be explicitly reconciled."""


class BudgetExceeded(RuntimeError):
    """The configured free allowance cannot cover the requested reservation."""


@dataclass(frozen=True)
class TaskSpec:
    command: str
    scope_id: str
    code_sha: str
    versions: tuple[tuple[str, str], ...] = ()
    family: str = "healthcheck"
    dependencies: tuple[str, ...] = ()
    priority: int = 0
    max_attempts: int = 3
    executor_type: str = "local"
    intent_id: str | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("unsupported task schema")
        if self.command not in COMMANDS:
            raise ValueError("unsupported command")
        for value in (self.scope_id, self.code_sha, self.family):
            validate_identifier(value)
        if self.intent_id is not None:
            validate_identifier(self.intent_id)
        if self.command in INTENT_COMMANDS and self.intent_id is None:
            raise ValueError("volatile observations and manual delivery require an intent_id")
        if self.executor_type not in ("local", "vercel", "actions"):
            raise ValueError("unsupported executor")
        if type(self.priority) is not int or not -1000 <= self.priority <= 1000:
            raise ValueError("invalid priority")
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 10:
            raise ValueError("invalid attempt limit")
        pairs = tuple(self.versions.items()) if isinstance(self.versions, Mapping) else tuple(tuple(pair) for pair in self.versions)
        for key, value in pairs:
            validate_identifier(key)
            validate_identifier(value)
        if len({key for key, _ in pairs}) != len(pairs):
            raise ValueError("duplicate input version")
        object.__setattr__(self, "versions", tuple(sorted(pairs)))
        if isinstance(self.dependencies, str):
            raise ValueError("dependencies must be a sequence of IDs")
        dependencies = tuple(self.dependencies)
        for dependency in dependencies:
            validate_identifier(dependency)
        if len(set(dependencies)) != len(dependencies):
            raise ValueError("duplicate dependency")
        object.__setattr__(self, "dependencies", tuple(sorted(dependencies)))

    @property
    def fingerprint(self) -> str:
        return _hash({
            "command": self.command, "scope_id": self.scope_id, "family": self.family,
            "code_sha": self.code_sha, "versions": self.versions, "dependencies": self.dependencies,
            "intent_id": self.intent_id,
            "task_schema_version": self.schema_version,
        })

    @property
    def request_signature(self) -> str:
        return _hash(asdict(self))

    @property
    def version_map(self) -> dict[str, str]:
        return dict(self.versions)


@dataclass(frozen=True)
class Job:
    job_id: str
    spec: TaskSpec
    state: str
    attempt: int
    generation: int
    owner: str | None
    expires_at: float | None
    result_id: str | None
    error_code: str | None
    next_attempt_at: float


@dataclass(frozen=True)
class ComputeLease:
    job_id: str
    worker_id: str
    code_sha: str
    generation: int

    def __post_init__(self) -> None:
        for value in (self.job_id, self.worker_id, self.code_sha):
            validate_identifier(value)
        if type(self.generation) is not int or self.generation < 1:
            raise ValueError("lease generation must be a positive integer")


@dataclass(frozen=True)
class Dispatch:
    dispatch_id: str
    job_id: str
    executor_type: str
    code_sha: str
    expires_at: float


@dataclass(frozen=True)
class PublicationClaim:
    publication_id: str
    job_id: str
    publisher_id: str
    generation: int
    code_sha: str
    commit_id: str
    resources: tuple[str, ...]

    def __post_init__(self) -> None:
        for value in (self.publication_id, self.job_id, self.publisher_id, self.code_sha, self.commit_id):
            validate_identifier(value)
        if type(self.generation) is not int or self.generation < 1:
            raise ValueError("publication generation must be a positive integer")
        if isinstance(self.resources, str):
            raise ValueError("resources must be a sequence")
        resources = tuple(self.resources)
        if not resources or len(set(resources)) != len(resources):
            raise ValueError("resources must be nonempty and unique")
        for resource in resources:
            validate_identifier(resource)
        object.__setattr__(self, "resources", tuple(sorted(resources)))


@dataclass(frozen=True)
class ConfigPublication:
    candidate_id: str
    config_id: str
    job_ids: tuple[str, ...]


@dataclass(frozen=True)
class BudgetNeed:
    platform: str
    period: str
    units: int

    def __post_init__(self) -> None:
        validate_identifier(self.platform)
        validate_identifier(self.period)
        if type(self.units) is not int or self.units < 1:
            raise ValueError("reservation units must be positive integers")


def validate_identifier(value: object) -> None:
    if not isinstance(value, str) or not value or value != value.strip() or any(ord(char) < 32 for char in value):
        raise ValueError("identifiers must be nonempty strings without surrounding whitespace or controls")


def _hash(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
