CREATE TABLE report_outbox (
    outbox_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE,
    spec_json TEXT NOT NULL,
    content_hash TEXT NOT NULL
);
CREATE TABLE report_parts (
    outbox_id TEXT NOT NULL REFERENCES report_outbox(outbox_id),
    ordinal INTEGER NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('queued','sending','sent','delivery_uncertain','failed')),
    attempt INTEGER NOT NULL DEFAULT 0,
    generation INTEGER NOT NULL DEFAULT 0,
    worker_id TEXT,
    next_attempt_at REAL NOT NULL DEFAULT 0,
    message_id TEXT,
    sent_at REAL,
    error_code TEXT,
    PRIMARY KEY(outbox_id,ordinal)
);
CREATE INDEX report_parts_claim ON report_parts(state,next_attempt_at);
CREATE UNIQUE INDEX report_message_identity ON report_parts(message_id) WHERE message_id IS NOT NULL;
CREATE TABLE outbox_requests (
    request_id TEXT PRIMARY KEY,
    outbox_id TEXT NOT NULL REFERENCES report_outbox(outbox_id)
);
