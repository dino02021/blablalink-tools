CREATE TABLE raid_source_followups (
    adoption_id TEXT PRIMARY KEY REFERENCES raid_source_adoptions(adoption_id),
    job_id TEXT NOT NULL REFERENCES jobs(job_id),
    problem_id TEXT NOT NULL REFERENCES objects(object_id)
);
