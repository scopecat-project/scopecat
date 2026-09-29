"""Immutable author source revision storage schema."""

AUTHOR_REVISION_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS author_revisions (
    content_hash TEXT PRIMARY KEY,
    bundle_digest TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS driver_source_selections (
    operation_id TEXT PRIMARY KEY,
    record_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS driver_source_head (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    operation_id TEXT NOT NULL REFERENCES driver_source_selections(operation_id)
);
"""
