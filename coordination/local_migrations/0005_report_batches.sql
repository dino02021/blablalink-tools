CREATE TABLE report_send_candidates (
    candidate_id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL,
    intent_id TEXT NOT NULL,
    serialized TEXT NOT NULL,
    is_accepted INTEGER NOT NULL DEFAULT 0,
    UNIQUE(scope_id,intent_id)
);
CREATE TABLE report_batch_requests (
    request_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES report_send_candidates(candidate_id)
);
