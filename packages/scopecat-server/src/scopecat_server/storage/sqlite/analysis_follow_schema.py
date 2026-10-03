"""Durable complete-group analysis progress."""

ANALYSIS_FOLLOW_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS analysis_follows (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    request_json TEXT NOT NULL,
    state TEXT NOT NULL,
    scan_index INTEGER NOT NULL DEFAULT 0,
    group_count INTEGER,
    error TEXT
);
CREATE TABLE IF NOT EXISTS analysis_follow_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    follow_id TEXT NOT NULL REFERENCES analysis_follows(id) ON DELETE CASCADE,
    group_index INTEGER NOT NULL,
    state TEXT NOT NULL,
    event_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS analysis_follow_group_progress
ON analysis_follow_events(follow_id, group_index, sequence);
"""
