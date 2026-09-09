"""Immutable plan revisions and a hideable named head; history is never deleted."""

EXPERIMENT_PLAN_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS experiment_plan_heads (
    plan_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL,
    hidden INTEGER NOT NULL DEFAULT 0 CHECK(hidden IN (0, 1))
);
CREATE TABLE IF NOT EXISTS experiment_plan_revisions (
    plan_id TEXT NOT NULL REFERENCES experiment_plan_heads(plan_id),
    revision INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    digest TEXT NOT NULL,
    PRIMARY KEY(plan_id, revision)
);
"""
