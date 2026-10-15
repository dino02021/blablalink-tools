CREATE TABLE raid_input_heads (
    scope_id TEXT PRIMARY KEY,
    problem_id TEXT NOT NULL REFERENCES objects(object_id),
    revision INTEGER NOT NULL
);
CREATE TABLE raid_drafts (
    draft_id TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    problem_id TEXT NOT NULL REFERENCES objects(object_id),
    final_id TEXT NOT NULL REFERENCES objects(object_id),
    view_id TEXT NOT NULL REFERENCES objects(object_id),
    PRIMARY KEY(scope_id,draft_id)
);
CREATE TABLE raid_confirmations (
    confirmation_id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL,
    intent_id TEXT NOT NULL,
    signature TEXT NOT NULL,
    draft_id TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE(scope_id,intent_id),
    FOREIGN KEY(scope_id,draft_id) REFERENCES raid_drafts(scope_id,draft_id)
);
CREATE TABLE raid_confirmed_heads (
    scope_id TEXT PRIMARY KEY,
    confirmation_id TEXT NOT NULL REFERENCES raid_confirmations(confirmation_id)
);
CREATE TABLE raid_confirmation_requests (
    request_id TEXT PRIMARY KEY,
    confirmation_id TEXT NOT NULL REFERENCES raid_confirmations(confirmation_id)
);
