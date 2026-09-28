"""Application devices and retained physical access aliases."""

DEVICE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS devices (
    device_id TEXT PRIMARY KEY,
    record_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS device_connection_revisions (
    revision_id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL REFERENCES devices(device_id),
    record_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS device_access_aliases (
    alias TEXT PRIMARY KEY,
    device_id TEXT NOT NULL REFERENCES devices(device_id)
);
CREATE TABLE IF NOT EXISTS device_connection_tests (
    operation_id TEXT PRIMARY KEY,
    revision_id TEXT NOT NULL REFERENCES device_connection_revisions(revision_id),
    record_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS setup_definitions (
    definition_id TEXT PRIMARY KEY,
    record_json TEXT NOT NULL
);
"""
