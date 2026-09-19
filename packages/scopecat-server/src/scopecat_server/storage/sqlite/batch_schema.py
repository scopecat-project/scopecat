"""Batch catalog and indexed run provenance; legacy evidence stays unscoped."""

EXPERIMENTAL_BATCH_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS experimental_batches (
    batch_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS run_sample_batches (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    role TEXT NOT NULL,
    batch_id TEXT NOT NULL REFERENCES experimental_batches(batch_id),
    PRIMARY KEY (run_id,role)
);
CREATE INDEX IF NOT EXISTS run_sample_batches_batch_run
ON run_sample_batches(batch_id,run_id);
"""
