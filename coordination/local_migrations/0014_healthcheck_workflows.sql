CREATE TABLE healthcheck_workflow_heads (
    scope_id TEXT PRIMARY KEY,
    policy_id TEXT NOT NULL REFERENCES objects(object_id),
    revision INTEGER NOT NULL CHECK(revision > 0)
);
CREATE TABLE healthcheck_workflow_policy_receipts (
    receipt_id TEXT PRIMARY KEY,
    signature TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE healthcheck_workflow_policy_requests (
    request_id TEXT PRIMARY KEY,
    receipt_id TEXT NOT NULL REFERENCES healthcheck_workflow_policy_receipts(receipt_id)
);
CREATE TABLE healthcheck_workflow_state (
    scope_id TEXT PRIMARY KEY REFERENCES healthcheck_workflow_heads(scope_id),
    policy_id TEXT NOT NULL REFERENCES objects(object_id),
    state_json TEXT NOT NULL,
    state_hash TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision > 0),
    updated_at REAL NOT NULL
);
