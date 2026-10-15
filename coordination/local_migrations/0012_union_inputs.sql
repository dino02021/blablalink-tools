CREATE TABLE union_input_heads (
    scope_id TEXT PRIMARY KEY,
    input_id TEXT NOT NULL REFERENCES objects(object_id),
    revision INTEGER NOT NULL CHECK(revision > 0),
    roster_state TEXT NOT NULL CHECK(roster_state IN ('active','inactive'))
);
CREATE TABLE roster_candidates (
    candidate_id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL,
    base_input_id TEXT NOT NULL REFERENCES objects(object_id),
    base_revision INTEGER NOT NULL CHECK(base_revision > 0),
    observation_id TEXT NOT NULL REFERENCES objects(object_id),
    target_input_id TEXT NOT NULL REFERENCES objects(object_id),
    plan_id TEXT NOT NULL,
    order_policy TEXT NOT NULL,
    code_sha TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE union_input_adoptions (
    adoption_id TEXT PRIMARY KEY,
    signature TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE union_input_requests (
    request_id TEXT PRIMARY KEY,
    adoption_id TEXT NOT NULL REFERENCES union_input_adoptions(adoption_id)
);
