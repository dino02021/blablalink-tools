CREATE TABLE raid_source_candidates (
    candidate_id TEXT PRIMARY KEY REFERENCES objects(object_id),
    scope_id TEXT NOT NULL
);
CREATE TABLE raid_source_heads (
    scope_id TEXT PRIMARY KEY,
    bundle_id TEXT NOT NULL REFERENCES objects(object_id),
    revision INTEGER NOT NULL CHECK(revision > 0)
);
CREATE TABLE raid_source_adoptions (
    adoption_id TEXT PRIMARY KEY,
    signature TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE raid_source_requests (
    request_id TEXT PRIMARY KEY,
    adoption_id TEXT NOT NULL REFERENCES raid_source_adoptions(adoption_id)
);
