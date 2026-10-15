ALTER TABLE control_nonces ADD COLUMN signed_at INTEGER;
CREATE TABLE control_request_usage (
    bucket_type TEXT NOT NULL,
    bucket_key TEXT NOT NULL,
    slot INTEGER NOT NULL,
    used INTEGER NOT NULL,
    PRIMARY KEY(bucket_type,bucket_key,slot)
);
CREATE TABLE control_clock_watermark (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    observed_at INTEGER NOT NULL
);
