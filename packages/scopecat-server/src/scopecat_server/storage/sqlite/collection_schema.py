"""Stable acquisition addresses; existing scheduler sequences remain unchanged."""

RECORD_COLLECTION_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS record_collections (
    collection_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    is_default INTEGER NOT NULL CHECK (is_default IN (0,1))
);
CREATE UNIQUE INDEX IF NOT EXISTS one_default_record_collection
ON record_collections(is_default) WHERE is_default=1;

INSERT OR IGNORE INTO record_collections
SELECT identity || ':records', 'Default records', '', 1,
       strftime('%Y-%m-%dT%H:%M:%f+00:00','now'),
       strftime('%Y-%m-%dT%H:%M:%f+00:00','now'), 1
FROM project_identity WHERE singleton=1;

CREATE TABLE IF NOT EXISTS run_addresses (
    run_id TEXT PRIMARY KEY REFERENCES scheduler_runs(run_id),
    collection_id TEXT NOT NULL REFERENCES record_collections(collection_id),
    number INTEGER NOT NULL CHECK (number >= 1),
    UNIQUE(collection_id,number)
);
"""

RECORD_COLLECTION_BACKFILL_SQL = """
INSERT INTO run_addresses(run_id,collection_id,number)
SELECT r.run_id,c.collection_id,r.sequence FROM scheduler_runs r
CROSS JOIN record_collections c WHERE c.is_default=1;
"""
