"""SQLite configuration-registry tables."""

CONFIG_REGISTRY_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS parameter_revisions (
    revision_id TEXT PRIMARY KEY,
    record_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS configuration_setup_contents (
    content_hash TEXT PRIMARY KEY,
    setup_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS parameter_branch_commits (
    name TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK (generation >= 1),
    revision_id TEXT NOT NULL REFERENCES parameter_revisions(revision_id),
    record_json TEXT NOT NULL,
    intent_hash TEXT NOT NULL,
    PRIMARY KEY (name, generation)
);
CREATE TABLE IF NOT EXISTS config_registry_entries (
    entry_id TEXT PRIMARY KEY,
    config_ref TEXT NOT NULL UNIQUE,
    entry_json TEXT NOT NULL,
    parameters_json TEXT NOT NULL,
    setup_content_hash TEXT NOT NULL
        REFERENCES configuration_setup_contents(content_hash),
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS config_registry_activations (
    generation INTEGER PRIMARY KEY CHECK (generation >= 1),
    entry_id TEXT NOT NULL,
    record_json TEXT NOT NULL,
    FOREIGN KEY (entry_id)
        REFERENCES config_registry_entries(entry_id)
);

CREATE TABLE IF NOT EXISTS parameter_workspace_heads (
    workspace_id TEXT PRIMARY KEY REFERENCES config_registry_entries(entry_id),
    entry_id TEXT NOT NULL REFERENCES config_registry_entries(entry_id)
);

CREATE TABLE IF NOT EXISTS config_operations (
    operation_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (
        kind IN ('activate_entry', 'publish_revision',
            'publish_calibration', 'publish_context')
    ),
    intent_hash TEXT NOT NULL,
    expected_generation INTEGER CHECK (expected_generation >= 0),
    result_entry_id TEXT NOT NULL,
    result_activation_generation INTEGER CHECK (
        result_activation_generation >= 1
    ),
    receipt_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    CHECK (
        (kind IN ('publish_context', 'publish_calibration')
            AND expected_generation IS NULL
            AND result_activation_generation IS NULL)
        OR (kind NOT IN ('publish_context', 'publish_calibration')
            AND expected_generation IS NOT NULL
            AND result_activation_generation IS NOT NULL AND (
                result_activation_generation = expected_generation
                OR result_activation_generation = expected_generation + 1))
    ),
    FOREIGN KEY (result_entry_id)
        REFERENCES config_registry_entries(entry_id),
    FOREIGN KEY (result_activation_generation)
        REFERENCES config_registry_activations(generation)
);
"""
