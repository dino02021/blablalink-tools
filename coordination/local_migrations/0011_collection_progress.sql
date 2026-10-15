CREATE TABLE collection_progress (
    job_id TEXT PRIMARY KEY REFERENCES jobs(job_id),
    metadata_json TEXT NOT NULL,
    metadata_hash TEXT NOT NULL,
    generation INTEGER NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE collection_source_progress (
    job_id TEXT NOT NULL REFERENCES collection_progress(job_id),
    source_key TEXT NOT NULL,
    state_json TEXT NOT NULL,
    state_hash TEXT NOT NULL,
    PRIMARY KEY(job_id,source_key)
);
CREATE TABLE collection_observations (
    job_id TEXT NOT NULL REFERENCES jobs(job_id),
    generation INTEGER NOT NULL,
    raw_snapshot_id TEXT NOT NULL REFERENCES objects(object_id),
    training_snapshot_id TEXT NOT NULL REFERENCES objects(object_id),
    checkpoint_id TEXT NOT NULL REFERENCES objects(object_id),
    captured_at TEXT NOT NULL,
    PRIMARY KEY(job_id,generation)
);
