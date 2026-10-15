ALTER TABLE config_candidates ADD COLUMN semantic_id TEXT;
CREATE UNIQUE INDEX config_candidate_semantic ON config_candidates(semantic_id) WHERE semantic_id IS NOT NULL;
