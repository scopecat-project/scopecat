"""Practice ownership and persistent cleanup admission fences."""

PRACTICE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS practice_scopes (
    scope_id TEXT PRIMARY KEY,
    record_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS practice_resources (
    kind TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    scope_id TEXT NOT NULL REFERENCES practice_scopes(scope_id),
    PRIMARY KEY(kind, resource_id)
);
CREATE INDEX IF NOT EXISTS practice_resources_scope
ON practice_resources(scope_id, kind, resource_id);
"""
