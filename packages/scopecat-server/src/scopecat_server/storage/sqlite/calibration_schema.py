"""SQLite durable calibration-cohort tables."""

CALIBRATION_COHORT_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS calibration_cohorts (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    cohort_id TEXT NOT NULL UNIQUE,
    fanout_scope TEXT NOT NULL,
    cohort_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS calibration_cohorts_scope_sequence
ON calibration_cohorts(fanout_scope, sequence DESC);

CREATE TABLE IF NOT EXISTS calibration_cohort_members (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    cohort_id TEXT NOT NULL
        REFERENCES calibration_cohorts(cohort_id) ON DELETE CASCADE,
    member_index INTEGER NOT NULL CHECK (member_index >= 0),
    member_id TEXT NOT NULL,
    calibration_key TEXT NOT NULL,
    procedure_run_id TEXT NOT NULL UNIQUE
        REFERENCES procedure_runs(procedure_run_id) ON DELETE RESTRICT,
    closure_status TEXT CHECK (
        closure_status IS NULL
        OR closure_status IN ('succeeded', 'failed', 'cancelled')
    ),
    closed_at TEXT,
    member_json TEXT NOT NULL,
    UNIQUE (cohort_id, member_index),
    UNIQUE (cohort_id, member_id),
    UNIQUE (cohort_id, calibration_key),
    UNIQUE (cohort_id, member_id, procedure_run_id),
    CHECK (
        (closure_status IS NULL AND closed_at IS NULL)
        OR (closure_status IS NOT NULL AND closed_at IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS calibration_cohort_members_key_sequence
ON calibration_cohort_members(calibration_key, sequence DESC);

CREATE INDEX IF NOT EXISTS calibration_cohort_members_success_key_sequence
ON calibration_cohort_members(calibration_key, sequence DESC)
WHERE closure_status = 'succeeded';

CREATE TRIGGER IF NOT EXISTS calibration_cohort_members_sync_terminal_closure
AFTER UPDATE OF closure_status, closed_at ON procedure_runs
FOR EACH ROW
WHEN NEW.state = 'closed'
BEGIN
    UPDATE calibration_cohort_members
    SET closure_status = NEW.closure_status,
        closed_at = NEW.closed_at
    WHERE procedure_run_id = NEW.procedure_run_id;
END;

CREATE TABLE IF NOT EXISTS calibration_success_publications (
    procedure_run_id TEXT PRIMARY KEY,
    cohort_id TEXT NOT NULL,
    member_id TEXT NOT NULL,
    calibration_key TEXT NOT NULL,
    operation_id TEXT NOT NULL
        REFERENCES config_operations(operation_id) ON DELETE RESTRICT,
    source_intent_hash TEXT NOT NULL,
    result_input_fingerprint TEXT NOT NULL,
    result_freshness_fingerprint TEXT NOT NULL,
    result_entry_id TEXT NOT NULL
        REFERENCES config_registry_entries(entry_id) ON DELETE RESTRICT,
    result_config_ref TEXT NOT NULL,
    result_content_hash TEXT NOT NULL,
    published_at TEXT NOT NULL,
    publication_json TEXT NOT NULL,
    UNIQUE (cohort_id, member_id),
    FOREIGN KEY (cohort_id, member_id, procedure_run_id)
        REFERENCES calibration_cohort_members(
            cohort_id, member_id, procedure_run_id
        ) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS calibration_success_publications_operation
ON calibration_success_publications(operation_id);

CREATE TABLE IF NOT EXISTS calibration_cohort_finalizations (
    cohort_id TEXT PRIMARY KEY
        REFERENCES calibration_cohorts(cohort_id) ON DELETE CASCADE,
    spec_hash TEXT NOT NULL,
    policy_id TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    policy_fingerprint TEXT NOT NULL,
    policy_json TEXT NOT NULL,
    calibration_definition_id TEXT NOT NULL,
    calibration_definition_version TEXT NOT NULL,
    calibration_definition_fingerprint TEXT NOT NULL,
    composition_policy_id TEXT NOT NULL,
    composition_policy_version TEXT NOT NULL,
    composition_policy_fingerprint TEXT NOT NULL,
    base_entry_id TEXT NOT NULL,
    owner_workspace_id TEXT NOT NULL,
    setup_content_hash TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    state TEXT NOT NULL CHECK (
        state IN (
            'waiting',
            'ready',
            'attention_required',
            'failed',
            'superseded',
            'published'
        )
    ),
    attempt_count INTEGER NOT NULL CHECK (attempt_count >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    ready_at TEXT,
    available_at TEXT,
    attention_actor TEXT,
    attention_reason TEXT,
    attention_required_at TEXT,
    failed_at TEXT,
    supersession_json TEXT,
    superseded_at TEXT,
    publication_operation_id TEXT
        REFERENCES config_operations(operation_id) ON DELETE RESTRICT,
    published_at TEXT,
    CHECK (
        (
            state = 'waiting'
            AND ready_at IS NULL
            AND available_at IS NULL
            AND attention_actor IS NULL
            AND attention_reason IS NULL
            AND attention_required_at IS NULL
            AND failed_at IS NULL
            AND supersession_json IS NULL
            AND superseded_at IS NULL
            AND publication_operation_id IS NULL
            AND published_at IS NULL
        )
        OR (
            state = 'ready'
            AND ready_at IS NOT NULL
            AND available_at IS NOT NULL
            AND attention_actor IS NULL
            AND attention_reason IS NULL
            AND attention_required_at IS NULL
            AND failed_at IS NULL
            AND supersession_json IS NULL
            AND superseded_at IS NULL
            AND publication_operation_id IS NULL
            AND published_at IS NULL
        )
        OR (
            state = 'attention_required'
            AND ready_at IS NOT NULL
            AND available_at IS NULL
            AND attention_actor IS NOT NULL
            AND attention_reason IS NOT NULL
            AND attention_required_at IS NOT NULL
            AND failed_at IS NULL
            AND supersession_json IS NULL
            AND superseded_at IS NULL
            AND publication_operation_id IS NULL
            AND published_at IS NULL
        )
        OR (
            state = 'failed'
            AND ready_at IS NULL
            AND available_at IS NULL
            AND attention_actor IS NULL
            AND attention_reason IS NULL
            AND attention_required_at IS NULL
            AND failed_at IS NOT NULL
            AND supersession_json IS NULL
            AND superseded_at IS NULL
            AND publication_operation_id IS NULL
            AND published_at IS NULL
        )
        OR (
            state = 'superseded'
            AND available_at IS NULL
            AND attention_actor IS NULL
            AND attention_reason IS NULL
            AND attention_required_at IS NULL
            AND failed_at IS NULL
            AND supersession_json IS NOT NULL
            AND superseded_at IS NOT NULL
            AND publication_operation_id IS NULL
            AND published_at IS NULL
        )
        OR (
            state = 'published'
            AND ready_at IS NOT NULL
            AND available_at IS NULL
            AND attention_actor IS NULL
            AND attention_reason IS NULL
            AND attention_required_at IS NULL
            AND failed_at IS NULL
            AND supersession_json IS NULL
            AND superseded_at IS NULL
            AND publication_operation_id IS NOT NULL
            AND published_at IS NOT NULL
        )
    )
);

CREATE INDEX IF NOT EXISTS calibration_cohort_finalizations_state
ON calibration_cohort_finalizations(owner_workspace_id, state, base_entry_id);

CREATE INDEX IF NOT EXISTS calibration_finalizations_setup
ON calibration_cohort_finalizations(state, setup_content_hash);

CREATE INDEX IF NOT EXISTS calibration_cohort_finalizations_ready_capability
ON calibration_cohort_finalizations(
    policy_id,
    policy_version,
    policy_fingerprint,
    calibration_definition_id,
    calibration_definition_version,
    calibration_definition_fingerprint,
    composition_policy_id,
    composition_policy_version,
    composition_policy_fingerprint,
    available_at,
    cohort_id
)
WHERE state = 'ready';

CREATE TABLE IF NOT EXISTS calibration_publication_ready_queue (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    cohort_id TEXT NOT NULL UNIQUE
        REFERENCES calibration_cohort_finalizations(cohort_id) ON DELETE CASCADE,
    enqueued_at TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS calibration_publication_sync_terminal_failure
AFTER UPDATE OF closure_status, closed_at ON calibration_cohort_members
FOR EACH ROW
WHEN NEW.closure_status IN ('failed', 'cancelled')
BEGIN
    UPDATE calibration_cohort_finalizations
    SET revision = revision + 1,
        state = 'failed',
        updated_at = NEW.closed_at,
        failed_at = NEW.closed_at
    WHERE cohort_id = NEW.cohort_id
      AND state = 'waiting';
END;

CREATE TRIGGER IF NOT EXISTS calibration_publication_sync_terminal_success
AFTER UPDATE OF closure_status, closed_at ON calibration_cohort_members
FOR EACH ROW
WHEN NEW.closure_status = 'succeeded'
BEGIN
    UPDATE calibration_cohort_finalizations
    SET revision = revision + 1,
        state = 'ready',
        updated_at = NEW.closed_at,
        ready_at = NEW.closed_at,
        available_at = NEW.closed_at
    WHERE cohort_id = NEW.cohort_id
      AND state = 'waiting'
      AND NOT EXISTS (
          SELECT 1
          FROM calibration_cohort_members AS member
          WHERE member.cohort_id = NEW.cohort_id
            AND (
                member.closure_status IS NULL
                OR member.closure_status <> 'succeeded'
            )
      );

    INSERT INTO calibration_publication_ready_queue(
        cohort_id,
        enqueued_at
    )
    SELECT cohort_id,
           ready_at
    FROM calibration_cohort_finalizations
    WHERE cohort_id = NEW.cohort_id
      AND state = 'ready';
END;
"""


__all__ = ["CALIBRATION_COHORT_TABLES_SQL"]
