-- Local SQLite adapter schema. D1 deployment/transaction parity is not yet verified.
CREATE TABLE objects (
    object_id TEXT PRIMARY KEY,
    serialized TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE config_candidates (
    candidate_id TEXT PRIMARY KEY,
    config_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    document_id TEXT NOT NULL REFERENCES objects(object_id),
    base_config_id TEXT REFERENCES objects(object_id),
    created_at REAL NOT NULL,
    published_at REAL,
    target_signature TEXT
);
CREATE TABLE config_versions (
    config_id TEXT PRIMARY KEY REFERENCES objects(object_id),
    candidate_id TEXT NOT NULL UNIQUE REFERENCES config_candidates(candidate_id),
    config_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    published_at REAL NOT NULL
);
CREATE TABLE config_heads (
    config_type TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    config_id TEXT NOT NULL REFERENCES config_versions(config_id),
    revision INTEGER NOT NULL,
    PRIMARY KEY (config_type, scope_id)
);
CREATE TABLE jobs (
    job_id TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL UNIQUE,
    spec_json TEXT NOT NULL,
    command TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    family TEXT NOT NULL,
    code_sha TEXT NOT NULL,
    executor_type TEXT NOT NULL,
    priority INTEGER NOT NULL,
    max_attempts INTEGER NOT NULL,
    state TEXT NOT NULL CHECK (state IN (
        'queued','dispatched','running','computed','publishing','succeeded','retry_wait','failed',
        'superseded','cancelled','auth_required','capacity_blocked','waiting_for_data','publish_uncertain'
    )),
    attempt INTEGER NOT NULL DEFAULT 0,
    generation INTEGER NOT NULL DEFAULT 0,
    owner TEXT,
    expires_at REAL,
    result_id TEXT REFERENCES objects(object_id),
    error_code TEXT,
    next_attempt_at REAL NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX jobs_claim ON jobs(state, executor_type, code_sha, next_attempt_at, priority);
CREATE INDEX jobs_expiry ON jobs(state, expires_at);
CREATE TABLE job_dependencies (
    job_id TEXT NOT NULL REFERENCES jobs(job_id),
    parent_id TEXT NOT NULL REFERENCES jobs(job_id),
    PRIMARY KEY(job_id, parent_id),
    CHECK (job_id != parent_id)
);
CREATE INDEX dependencies_parent ON job_dependencies(parent_id);
CREATE TABLE requests (
    request_id TEXT PRIMARY KEY,
    signature TEXT NOT NULL,
    job_id TEXT NOT NULL REFERENCES jobs(job_id),
    created_at REAL NOT NULL
);
CREATE TABLE desired_targets (
    scope_id TEXT NOT NULL,
    family TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    PRIMARY KEY(scope_id, family)
);
CREATE TABLE candidate_jobs (
    candidate_id TEXT NOT NULL REFERENCES config_candidates(candidate_id),
    job_id TEXT NOT NULL REFERENCES jobs(job_id),
    PRIMARY KEY(candidate_id, job_id)
);
CREATE TABLE job_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL REFERENCES jobs(job_id),
    state TEXT NOT NULL,
    error_code TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX events_job ON job_events(job_id, event_id);
CREATE TABLE job_checkpoints (
    job_id TEXT PRIMARY KEY REFERENCES jobs(job_id),
    object_id TEXT NOT NULL REFERENCES objects(object_id),
    generation INTEGER NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE dispatches (
    dispatch_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(job_id),
    dispatcher_id TEXT NOT NULL,
    executor_type TEXT NOT NULL,
    code_sha TEXT NOT NULL,
    budget_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('pending','acknowledged','expired','cancelled')),
    executor_run_id TEXT,
    worker_id TEXT,
    lease_generation INTEGER,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE UNIQUE INDEX dispatch_pending ON dispatches(job_id) WHERE state='pending';
CREATE INDEX dispatch_expiry ON dispatches(state,expires_at);
CREATE INDEX dispatch_job ON dispatches(job_id,created_at);
CREATE TABLE publications (
    publication_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(job_id),
    publisher_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    commit_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('publishing','publish_uncertain','succeeded')),
    resources_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE resource_locks (
    resource_id TEXT PRIMARY KEY,
    publication_id TEXT NOT NULL REFERENCES publications(publication_id)
);
CREATE INDEX publication_state ON publications(state);
CREATE INDEX publication_job ON publications(job_id,state);
CREATE INDEX resource_publication ON resource_locks(publication_id);
CREATE TABLE publication_resources (
    publication_id TEXT NOT NULL REFERENCES publications(publication_id),
    resource_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('pending','verified','unknown')),
    observed_commit_id TEXT,
    updated_at REAL NOT NULL,
    PRIMARY KEY(publication_id,resource_id)
);
CREATE TABLE usage_budget (
    platform TEXT NOT NULL,
    period TEXT NOT NULL,
    limit_units INTEGER NOT NULL CHECK(limit_units >= 0),
    used_units INTEGER NOT NULL DEFAULT 0 CHECK(used_units >= 0),
    reserved_units INTEGER NOT NULL DEFAULT 0 CHECK(reserved_units >= 0),
    PRIMARY KEY(platform, period)
);
CREATE TABLE budget_reservations (
    reservation_id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    period TEXT NOT NULL,
    units INTEGER NOT NULL CHECK(units > 0),
    actual_units INTEGER CHECK(actual_units >= 0),
    job_id TEXT REFERENCES jobs(job_id),
    FOREIGN KEY(platform, period) REFERENCES usage_budget(platform, period)
);
CREATE TABLE dispatch_budgets (
    dispatch_id TEXT NOT NULL REFERENCES dispatches(dispatch_id),
    reservation_id TEXT NOT NULL REFERENCES budget_reservations(reservation_id),
    PRIMARY KEY(dispatch_id,reservation_id)
);
CREATE INDEX reservation_job ON budget_reservations(job_id);
CREATE TABLE schedule_slots (
    schedule_id TEXT NOT NULL,
    slot_utc INTEGER NOT NULL,
    job_id TEXT NOT NULL REFERENCES jobs(job_id),
    signature TEXT NOT NULL,
    PRIMARY KEY(schedule_id, slot_utc)
);
