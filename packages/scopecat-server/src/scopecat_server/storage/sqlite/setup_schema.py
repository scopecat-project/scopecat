"""Independent executable setup authority and immutable revisions."""

SETUP_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS setup_revisions (
    revision_id TEXT PRIMARY KEY,
    record_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS setup_activations (
    generation INTEGER PRIMARY KEY CHECK (generation >= 1),
    revision_id TEXT NOT NULL REFERENCES setup_revisions(revision_id),
    record_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS setup_activation_operations (
    operation_id TEXT PRIMARY KEY,
    generation INTEGER NOT NULL REFERENCES setup_activations(generation),
    record_json TEXT NOT NULL
);
"""
