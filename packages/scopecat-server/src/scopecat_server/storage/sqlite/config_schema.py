"""SQLite configuration-registry tables."""

CONFIG_REGISTRY_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS config_registry_entries (
    entry_id TEXT PRIMARY KEY,
    config_ref TEXT NOT NULL UNIQUE,
    entry_json TEXT NOT NULL,
    config_json TEXT NOT NULL,
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
        kind IN ('activate_entry', 'publish_revision', 'publish_calibration')
    ),
    intent_hash TEXT NOT NULL,
    expected_generation INTEGER NOT NULL CHECK (expected_generation >= 0),
    result_entry_id TEXT NOT NULL,
    result_activation_generation INTEGER NOT NULL CHECK (
        result_activation_generation >= 1
    ),
    receipt_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    CHECK (
        result_activation_generation = expected_generation
        OR result_activation_generation = expected_generation + 1
    ),
    FOREIGN KEY (result_entry_id)
        REFERENCES config_registry_entries(entry_id),
    FOREIGN KEY (result_activation_generation)
        REFERENCES config_registry_activations(generation)
);
"""
