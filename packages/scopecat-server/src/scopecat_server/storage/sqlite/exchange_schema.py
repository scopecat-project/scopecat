"""Imported evidence has source-qualified identity, never execution ownership."""

EXCHANGE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS imported_captures (
    content_hash TEXT PRIMARY KEY,
    source_project_id TEXT NOT NULL,
    object_digest TEXT NOT NULL,
    roots_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS imported_run_identities (
    source_project_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    capture_hash TEXT NOT NULL REFERENCES imported_captures(content_hash),
    PRIMARY KEY (source_project_id, run_id, capture_hash)
);
"""
