CREATE TABLE healthcheck_workflow_tick_cursors (
    code_sha TEXT PRIMARY KEY,
    last_scope_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision > 0),
    updated_at REAL NOT NULL
);
