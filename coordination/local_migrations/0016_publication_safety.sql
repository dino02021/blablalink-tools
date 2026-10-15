CREATE TABLE publication_safety_phases (
    publication_id TEXT PRIMARY KEY REFERENCES publications(publication_id),
    is_send_started INTEGER NOT NULL CHECK(is_send_started IN (0,1))
);
CREATE TABLE publication_bases (
    publication_id TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    document_id TEXT NOT NULL REFERENCES objects(object_id),
    PRIMARY KEY(publication_id,resource_id),
    FOREIGN KEY(publication_id,resource_id) REFERENCES publication_resources(publication_id,resource_id)
);
CREATE TABLE publication_abort_receipts (
    receipt_id TEXT PRIMARY KEY,
    publication_id TEXT NOT NULL UNIQUE REFERENCES publications(publication_id),
    plan_id TEXT NOT NULL REFERENCES objects(object_id),
    signature TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE publication_abort_requests (
    request_id TEXT PRIMARY KEY,
    receipt_id TEXT NOT NULL REFERENCES publication_abort_receipts(receipt_id)
);
CREATE VIEW publication_effective_states AS
SELECT p.*,CASE WHEN a.receipt_id IS NOT NULL THEN 'aborted' ELSE p.state END AS effective_state
FROM publications p LEFT JOIN publication_abort_receipts a ON a.publication_id=p.publication_id;
