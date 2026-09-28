"""SQLite run snapshot, outcome, content, and object-binding tables."""

RUN_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    config_content_hash TEXT NOT NULL,
    config_source_json TEXT,
    execution_setup_json TEXT
);

CREATE TABLE IF NOT EXISTS run_outcomes (
    run_id TEXT PRIMARY KEY REFERENCES runs(run_id) ON DELETE CASCADE,
    result TEXT NOT NULL CHECK (result IN ('succeeded', 'failed', 'cancelled')),
    certainty TEXT NOT NULL CHECK (certainty IN ('known', 'indeterminate')),
    finished_at TEXT NOT NULL,
    outcome_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_contents (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('artifact', 'dataset', 'record')),
    content_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    produced_by TEXT,
    entry_json TEXT NOT NULL,
    UNIQUE (run_id, role, content_id)
);

CREATE INDEX IF NOT EXISTS run_contents_owner_role_kind_sequence
ON run_contents(run_id, role, kind, sequence DESC);

CREATE INDEX IF NOT EXISTS run_contents_owner_role_sequence
ON run_contents(run_id, role, sequence DESC);

CREATE INDEX IF NOT EXISTS run_contents_owner_kind_sequence
ON run_contents(run_id, kind, sequence DESC);

CREATE INDEX IF NOT EXISTS run_contents_owner_sequence
ON run_contents(run_id, sequence DESC);

CREATE TABLE IF NOT EXISTS run_repository_refs (
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    ref TEXT NOT NULL,
    digest TEXT NOT NULL,
    PRIMARY KEY (run_id, ref)
);
"""
