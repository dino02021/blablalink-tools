CREATE TABLE control_nonces (
    principal_id TEXT NOT NULL,
    key_id TEXT NOT NULL,
    nonce TEXT NOT NULL,
    envelope_hash TEXT NOT NULL,
    PRIMARY KEY(principal_id,key_id,nonce)
);
CREATE TABLE control_requests (
    principal_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    body_hash TEXT NOT NULL,
    request_key TEXT NOT NULL UNIQUE,
    actor_status TEXT NOT NULL DEFAULT 'unknown' CHECK(actor_status='unknown'),
    PRIMARY KEY(principal_id,request_id)
);
