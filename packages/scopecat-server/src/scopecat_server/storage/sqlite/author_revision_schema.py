"""Immutable author source revision storage schema."""

AUTHOR_REVISION_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS author_revisions (
    content_hash TEXT PRIMARY KEY,
    bundle_digest TEXT NOT NULL
);
"""
