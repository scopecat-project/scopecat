"""Additive target catalog tables; no inferred targets for retained evidence."""

TARGET_CATALOG_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS measurement_targets (
    target_id TEXT PRIMARY KEY,
    head_revision INTEGER NOT NULL CHECK (head_revision >= 1)
);
CREATE TABLE IF NOT EXISTS measurement_target_revisions (
    target_id TEXT NOT NULL REFERENCES measurement_targets(target_id),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    revision_json TEXT NOT NULL,
    PRIMARY KEY (target_id, revision)
);
"""
