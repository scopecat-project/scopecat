"""Independent executable setup authority and immutable revisions."""

SETUP_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS setup_revisions (
    revision_id TEXT PRIMARY KEY,
    record_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS application_initialization (
    id INTEGER PRIMARY KEY CHECK (id = 1)
);
"""
