"""Append-only descriptive apparatus objects and observations."""

APPARATUS_HISTORY_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS apparatus_objects (
    object_id TEXT PRIMARY KEY,
    head_revision INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS apparatus_object_revisions (
    object_id TEXT NOT NULL REFERENCES apparatus_objects(object_id),
    revision INTEGER NOT NULL,
    revision_json TEXT NOT NULL,
    PRIMARY KEY (object_id, revision)
);
CREATE TABLE IF NOT EXISTS apparatus_observations (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_id TEXT NOT NULL UNIQUE,
    object_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    observation_json TEXT NOT NULL,
    FOREIGN KEY (object_id, revision)
        REFERENCES apparatus_object_revisions(object_id, revision)
);
CREATE INDEX IF NOT EXISTS apparatus_observations_object_sequence
ON apparatus_observations(object_id, sequence);
CREATE TABLE IF NOT EXISTS apparatus_observation_runs (
    observation_id TEXT NOT NULL REFERENCES apparatus_observations(observation_id),
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    PRIMARY KEY (observation_id, run_id)
);
CREATE INDEX IF NOT EXISTS apparatus_observation_runs_run
ON apparatus_observation_runs(run_id, observation_id);
CREATE TABLE IF NOT EXISTS apparatus_observation_attachments (
    observation_id TEXT NOT NULL REFERENCES apparatus_observations(observation_id),
    digest TEXT NOT NULL,
    PRIMARY KEY (observation_id, digest)
);
"""
