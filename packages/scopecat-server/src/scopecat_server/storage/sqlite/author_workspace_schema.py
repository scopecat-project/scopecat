"""Workspace-owned mutable author state; legacy tables remain sealed evidence."""

AUTHOR_WORKSPACE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS author_workspaces (
    workspace_id TEXT PRIMARY KEY,
    name TEXT NOT NULL
);
INSERT OR IGNORE INTO author_workspaces VALUES ('legacy', 'Original workspace');
CREATE TABLE IF NOT EXISTS author_workspace_heads (
    workspace_id TEXT PRIMARY KEY REFERENCES author_workspaces(workspace_id),
    generation INTEGER NOT NULL,
    content_hash TEXT NOT NULL REFERENCES author_revisions(content_hash)
);
CREATE TABLE IF NOT EXISTS author_workspace_revisions (
    workspace_id TEXT NOT NULL REFERENCES author_workspaces(workspace_id),
    content_hash TEXT NOT NULL REFERENCES author_revisions(content_hash),
    PRIMARY KEY(workspace_id, content_hash)
);
CREATE TABLE IF NOT EXISTS author_workspace_preparations (
    workspace_id TEXT NOT NULL REFERENCES author_workspaces(workspace_id),
    operation_id TEXT NOT NULL,
    record_json TEXT NOT NULL,
    PRIMARY KEY(workspace_id, operation_id)
);
"""

AUTHOR_WORKSPACE_BACKFILL_SQL = """
INSERT INTO author_workspace_heads
SELECT 'legacy', generation, content_hash FROM author_revision_active;
INSERT INTO author_workspace_revisions
SELECT 'legacy', content_hash FROM author_revisions;
INSERT INTO author_workspace_preparations
SELECT 'legacy', operation_id, record_json FROM author_preparations;
"""
