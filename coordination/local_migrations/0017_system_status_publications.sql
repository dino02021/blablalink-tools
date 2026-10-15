CREATE TABLE system_status_route_heads (
    scope_id TEXT PRIMARY KEY,
    route_id TEXT NOT NULL REFERENCES objects(object_id),
    revision INTEGER NOT NULL CHECK(revision > 0)
);
CREATE TABLE system_status_route_receipts (
    receipt_id TEXT PRIMARY KEY,
    signature TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE system_status_route_requests (
    request_id TEXT PRIMARY KEY,
    receipt_id TEXT NOT NULL REFERENCES system_status_route_receipts(receipt_id)
);
CREATE TABLE system_status_observations (
    observation_id TEXT PRIMARY KEY REFERENCES objects(object_id),
    semantic_id TEXT NOT NULL UNIQUE,
    status_hash TEXT NOT NULL,
    route_id TEXT NOT NULL REFERENCES objects(object_id),
    last_checked_at REAL NOT NULL
);
CREATE TABLE system_status_occurrences (
    occurrence_id TEXT PRIMARY KEY REFERENCES objects(object_id),
    observation_id TEXT NOT NULL REFERENCES system_status_observations(observation_id),
    revision INTEGER NOT NULL CHECK(revision > 0)
);
CREATE TABLE system_status_observation_heads (
    scope_id TEXT PRIMARY KEY,
    occurrence_id TEXT NOT NULL REFERENCES system_status_occurrences(occurrence_id),
    revision INTEGER NOT NULL CHECK(revision > 0)
);
