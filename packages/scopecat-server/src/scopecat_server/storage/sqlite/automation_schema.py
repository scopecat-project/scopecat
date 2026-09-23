"""SQLite durable procedure-run and worker-lease tables."""

AUTOMATION_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS procedure_runs (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    procedure_run_id TEXT NOT NULL UNIQUE,
    definition_id TEXT NOT NULL,
    definition_version TEXT NOT NULL,
    definition_fingerprint TEXT NOT NULL,
    request_key TEXT NOT NULL,
    intent_hash TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    state TEXT NOT NULL CHECK (
        state IN (
            'ready', 'leased', 'waiting_for_input', 'attention_required', 'closed'
        )
    ),
    closure_status TEXT CHECK (
        closure_status IS NULL
        OR closure_status IN ('succeeded', 'failed', 'cancelled')
    ),
    closed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    run_json TEXT NOT NULL,
    UNIQUE (definition_id, request_key),
    CHECK (
        (
            state = 'closed'
            AND closure_status IS NOT NULL
            AND closed_at IS NOT NULL
        )
        OR (
            state <> 'closed'
            AND closure_status IS NULL
            AND closed_at IS NULL
        )
    )
);

CREATE INDEX IF NOT EXISTS procedure_runs_state_sequence
ON procedure_runs(state, sequence);

CREATE INDEX IF NOT EXISTS procedure_runs_definition_state_sequence
ON procedure_runs(
    definition_id,
    definition_version,
    definition_fingerprint,
    state,
    sequence
);

CREATE TABLE IF NOT EXISTS calibration_check_requests (
    sequence INTEGER PRIMARY KEY REFERENCES procedure_runs(sequence) ON DELETE CASCADE,
    scope_hash TEXT NOT NULL,
    context_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calibration_tasks (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL UNIQUE,
    mode TEXT NOT NULL,
    record_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS calibration_tasks_mode_sequence
ON calibration_tasks(mode, sequence);

CREATE INDEX IF NOT EXISTS calibration_checks_scope_sequence
ON calibration_check_requests(scope_hash, sequence);
CREATE INDEX IF NOT EXISTS calibration_checks_context_sequence
ON calibration_check_requests(context_hash, sequence);
CREATE INDEX IF NOT EXISTS calibration_checks_scope_context_sequence
ON calibration_check_requests(scope_hash, context_hash, sequence);

CREATE TABLE IF NOT EXISTS procedure_step_attempts (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    procedure_run_id TEXT NOT NULL
        REFERENCES procedure_runs(procedure_run_id) ON DELETE CASCADE,
    step_key TEXT NOT NULL,
    attempt INTEGER NOT NULL CHECK (attempt >= 1),
    operation TEXT NOT NULL CHECK (
        operation IN (
            'run', 'analysis', 'config_activation', 'config_publish',
            'interpretation', 'parameter_publish'
        )
    ),
    intent_hash TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    state TEXT NOT NULL CHECK (
        state IN (
            'running', 'succeeded', 'failed', 'waiting_for_input',
            'attention_required'
        )
    ),
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    attempt_json TEXT NOT NULL,
    UNIQUE (procedure_run_id, step_key, attempt)
);

CREATE INDEX IF NOT EXISTS procedure_step_attempts_run_step_attempt
ON procedure_step_attempts(procedure_run_id, step_key, attempt DESC);

CREATE UNIQUE INDEX IF NOT EXISTS procedure_step_attempts_one_running
ON procedure_step_attempts(procedure_run_id)
WHERE state = 'running';

CREATE TABLE IF NOT EXISTS procedure_leases (
    procedure_run_id TEXT PRIMARY KEY
        REFERENCES procedure_runs(procedure_run_id) ON DELETE CASCADE,
    worker_id TEXT NOT NULL,
    token TEXT NOT NULL UNIQUE,
    acquired_at TEXT NOT NULL,
    renewed_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
"""


__all__ = ["AUTOMATION_TABLES_SQL"]
