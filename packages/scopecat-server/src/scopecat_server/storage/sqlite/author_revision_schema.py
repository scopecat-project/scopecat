"""Immutable author source revision storage schema."""

AUTHOR_REVISION_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS author_revisions (
    content_hash TEXT PRIMARY KEY,
    bundle_digest TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS author_revision_active (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    generation INTEGER NOT NULL,
    content_hash TEXT NOT NULL REFERENCES author_revisions(content_hash)
);
"""
