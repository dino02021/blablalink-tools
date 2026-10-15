CREATE TABLE route_registry_heads (
    scope_id TEXT PRIMARY KEY,
    route_snapshot_id TEXT NOT NULL REFERENCES objects(object_id),
    revision INTEGER NOT NULL CHECK (revision > 0)
);
CREATE TABLE sender_grants (
    scope_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    grant_id TEXT NOT NULL REFERENCES objects(object_id),
    revision INTEGER NOT NULL CHECK (revision > 0),
    PRIMARY KEY(scope_id,principal_id)
);
CREATE TABLE routing_changes (
    change_id TEXT PRIMARY KEY,
    signature TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0),
    created_at REAL NOT NULL
);
CREATE TABLE routing_change_requests (
    request_id TEXT PRIMARY KEY,
    change_id TEXT NOT NULL REFERENCES routing_changes(change_id)
);
CREATE TABLE authorized_report_batches (
    candidate_id TEXT PRIMARY KEY REFERENCES report_send_candidates(candidate_id),
    scope_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    route_snapshot_id TEXT NOT NULL REFERENCES objects(object_id),
    grant_id TEXT NOT NULL REFERENCES objects(object_id),
    grant_revision INTEGER NOT NULL
);
CREATE TABLE authorized_report_deliveries (
    outbox_id TEXT PRIMARY KEY REFERENCES report_outbox(outbox_id),
    candidate_id TEXT NOT NULL REFERENCES authorized_report_batches(candidate_id),
    semantic_id TEXT NOT NULL
);
CREATE INDEX authorized_delivery_semantic ON authorized_report_deliveries(semantic_id);
CREATE TABLE authorized_route_leases (
    outbox_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    generation INTEGER NOT NULL,
    scope_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    route_snapshot_id TEXT NOT NULL REFERENCES objects(object_id),
    grant_id TEXT NOT NULL REFERENCES objects(object_id),
    grant_revision INTEGER NOT NULL,
    is_started INTEGER NOT NULL DEFAULT 0 CHECK (is_started IN (0,1)),
    start_deadline REAL NOT NULL,
    started_at REAL,
    PRIMARY KEY(outbox_id,ordinal),
    FOREIGN KEY(outbox_id,ordinal) REFERENCES report_parts(outbox_id,ordinal)
);
CREATE INDEX authorized_route_scope ON authorized_route_leases(scope_id);
