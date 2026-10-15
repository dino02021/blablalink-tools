CREATE TABLE reference_candidates (
    candidate_id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL,
    document_id TEXT NOT NULL REFERENCES objects(object_id),
    base_reference_id TEXT REFERENCES objects(object_id),
    validation_code_sha TEXT NOT NULL,
    semantic_id TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL
);
CREATE TABLE reference_heads (
    scope_id TEXT PRIMARY KEY,
    reference_id TEXT NOT NULL REFERENCES objects(object_id),
    revision INTEGER NOT NULL CHECK (revision > 0)
);
CREATE TABLE reference_publications (
    publication_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL UNIQUE REFERENCES reference_candidates(candidate_id),
    signature TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0),
    published_at REAL NOT NULL
);
CREATE TABLE reference_publication_requests (
    request_id TEXT PRIMARY KEY,
    publication_id TEXT NOT NULL REFERENCES reference_publications(publication_id)
);
