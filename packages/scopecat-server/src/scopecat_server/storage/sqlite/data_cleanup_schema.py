"""Recoverable deletion receipts and retained resource fences."""

from scopecat_server.storage.sqlite.data_reference_schema import DATA_REFERENCE_COLUMNS

_tables = """
CREATE TABLE IF NOT EXISTS data_cleanup_operations (
    operation_id TEXT PRIMARY KEY,
    record_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deleted_resources (
    kind TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    operation_id TEXT NOT NULL REFERENCES data_cleanup_operations(operation_id),
    PRIMARY KEY(kind, resource_id)
);
CREATE INDEX IF NOT EXISTS deleted_resources_identity ON deleted_resources(resource_id);
"""

# Fence scientific references at their commit boundary, including requests that
# were validated before cleanup. Logs and deletion receipts deliberately retain
# deleted identities and are not scientific-reference publishers.
_triggers: list[str] = []
for _table, _identity_column, _column, _kind in DATA_REFERENCE_COLUMNS:
    for _event in ("INSERT", "UPDATE"):
        _triggers.append(f"""
CREATE TRIGGER IF NOT EXISTS cleanup_{_table}_{_column}_{_event}
BEFORE {_event} ON {_table}
WHEN NEW.{_column} IS NOT NULL AND EXISTS (
    SELECT 1 FROM json_tree(NEW.{_column}) j
    JOIN deleted_resources d ON d.resource_id=j.atom
    WHERE j.type='text'
)
BEGIN
    SELECT RAISE(ABORT, 'Scientific reference targets cleared data');
END;
""")  # noqa: S608 - fixed schema identifiers

DATA_CLEANUP_TABLES_SQL = _tables + "".join(_triggers)
