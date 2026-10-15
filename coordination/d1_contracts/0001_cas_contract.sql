-- Offline contract schema only. Not a migration for the current coordinator.
CREATE TABLE contract_heads (
    scope_id TEXT NOT NULL,
    family TEXT NOT NULL,
    version_id TEXT NOT NULL,
    code_sha TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision > 0),
    PRIMARY KEY(scope_id,family)
);
CREATE TABLE contract_receipts (
    request_id TEXT PRIMARY KEY,
    signature TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    family TEXT NOT NULL,
    base_revision INTEGER NOT NULL,
    version_id TEXT NOT NULL,
    code_sha TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision > 0)
);
CREATE TABLE contract_barriers (
    scope_id TEXT PRIMARY KEY,
    is_blocked INTEGER NOT NULL CHECK(is_blocked IN (0,1))
);
CREATE TABLE contract_cas_guards (
    request_id TEXT PRIMARY KEY,
    signature TEXT NOT NULL,
    is_allowed INTEGER NOT NULL CHECK(is_allowed = 1)
);
