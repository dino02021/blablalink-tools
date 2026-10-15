CREATE TABLE object_retention_receipts (
    plan_id TEXT PRIMARY KEY,
    object_ids_json TEXT NOT NULL,
    confirmed_at REAL NOT NULL
);
