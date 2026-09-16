-- Retained schema from public commit f9dd0762051c642be4a4324ea9edfc745a857ad5; development fixture, not stable baseline.
BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS project_schema (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    version INTEGER NOT NULL
);

INSERT OR IGNORE INTO project_schema(singleton, version)
VALUES (1, 68);

CREATE TABLE IF NOT EXISTS project_identity (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    identity TEXT NOT NULL UNIQUE
);

INSERT OR IGNORE INTO project_identity(singleton, identity)
VALUES (1, 'local:' || lower(hex(randomblob(16))));

CREATE TABLE IF NOT EXISTS scheduler_runs (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL CHECK (
        state IN ('queued', 'leased', 'attention_required', 'closed')
    ),
    updated_at TEXT NOT NULL,
    admission_json TEXT NOT NULL,
    attention_reason TEXT,
    cancellation_requested_at TEXT,
    CHECK (
        (state = 'attention_required' AND attention_reason IS NOT NULL)
        OR (state <> 'attention_required' AND attention_reason IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS scheduler_runs_state_sequence
ON scheduler_runs(state, sequence);

CREATE TABLE IF NOT EXISTS run_resource_claims (
    run_id TEXT NOT NULL REFERENCES scheduler_runs(run_id) ON DELETE CASCADE,
    resource_kind TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    PRIMARY KEY (run_id, resource_kind, resource_id)
);

CREATE TABLE IF NOT EXISTS durable_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS durable_events_run_id_event_id
ON durable_events(run_id, event_id);

CREATE TABLE IF NOT EXISTS run_execution_segments (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    segment_id TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL REFERENCES scheduler_runs(run_id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    executor_id TEXT NOT NULL,
    run_contract_fingerprint TEXT NOT NULL,
    started_at TEXT NOT NULL,
    start_point_count INTEGER NOT NULL CHECK (start_point_count >= 0),
    ended_at TEXT,
    end_point_count INTEGER CHECK (end_point_count >= 0),
    result TEXT CHECK (
        result IS NULL
        OR result IN ('succeeded', 'failed', 'cancelled', 'interrupted')
    ),
    certainty TEXT CHECK (
        certainty IS NULL OR certainty IN ('known', 'indeterminate')
    ),
    reason TEXT,
    UNIQUE (run_id, ordinal),
    CHECK (
        (
            ended_at IS NULL
            AND end_point_count IS NULL
            AND result IS NULL
            AND certainty IS NULL
            AND reason IS NULL
        ) OR (
            ended_at IS NOT NULL
            AND end_point_count IS NOT NULL
            AND result IS NOT NULL
            AND certainty IS NOT NULL
            AND (result <> 'interrupted' OR reason IS NOT NULL)
        )
    )
);

CREATE INDEX IF NOT EXISTS run_execution_segments_run_sequence
ON run_execution_segments(run_id, sequence);

CREATE TABLE IF NOT EXISTS executor_leases (
    run_id TEXT PRIMARY KEY REFERENCES scheduler_runs(run_id) ON DELETE CASCADE,
    segment_id TEXT NOT NULL UNIQUE
        REFERENCES run_execution_segments(segment_id) ON DELETE CASCADE,
    executor_id TEXT NOT NULL,
    token TEXT NOT NULL UNIQUE,
    acquired_at TEXT NOT NULL,
    renewed_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resource_claims (
    resource_kind TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    owner_kind TEXT NOT NULL CHECK (
        owner_kind IN ('run', 'instrument_session')
    ),
    owner_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'quarantined')),
    acquired_at TEXT NOT NULL,
    PRIMARY KEY (resource_kind, resource_id)
);

CREATE INDEX IF NOT EXISTS resource_claims_owner
ON resource_claims(owner_kind, owner_id);

CREATE TABLE IF NOT EXISTS instrument_sessions (
    session_id TEXT PRIMARY KEY,
    open_operation_id TEXT NOT NULL UNIQUE,
    actor TEXT NOT NULL,
    config_entry_id TEXT NOT NULL,
    config_content_hash TEXT NOT NULL,
    instrument_ids_json TEXT NOT NULL,
    exclusivity_keys_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN ('active', 'attention_required', 'closed')
    ),
    acquired_at TEXT NOT NULL,
    renewed_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    attention_reason TEXT,
    active_operation_id TEXT,
    active_operation_kind TEXT CHECK (
        active_operation_kind IS NULL
        OR active_operation_kind IN ('apply', 'invoke', 'collect')
    ),
    end_status TEXT CHECK (
        end_status IS NULL OR end_status IN ('closed', 'aborted')
    ),
    CHECK (
        (
            state = 'active'
            AND attention_reason IS NULL
            AND end_status IS NULL
        )
        OR (
            state = 'attention_required'
            AND attention_reason IS NOT NULL
            AND end_status IS NULL
        )
        OR (
            state = 'closed'
            AND attention_reason IS NULL
            AND active_operation_id IS NULL
            AND active_operation_kind IS NULL
            AND end_status IS NOT NULL
        )
    ),
    CHECK (
        (active_operation_id IS NULL) = (active_operation_kind IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS instrument_sessions_state
ON instrument_sessions(state);


CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    config_content_hash TEXT NOT NULL,
    config_source_json TEXT
);

CREATE TABLE IF NOT EXISTS run_outcomes (
    run_id TEXT PRIMARY KEY REFERENCES runs(run_id) ON DELETE CASCADE,
    result TEXT NOT NULL CHECK (result IN ('succeeded', 'failed', 'cancelled')),
    certainty TEXT NOT NULL CHECK (certainty IN ('known', 'indeterminate')),
    finished_at TEXT NOT NULL,
    outcome_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_contents (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('artifact', 'dataset', 'record')),
    content_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    produced_by TEXT,
    entry_json TEXT NOT NULL,
    UNIQUE (run_id, role, content_id)
);

CREATE INDEX IF NOT EXISTS run_contents_owner_role_kind_sequence
ON run_contents(run_id, role, kind, sequence DESC);

CREATE INDEX IF NOT EXISTS run_contents_owner_role_sequence
ON run_contents(run_id, role, sequence DESC);

CREATE INDEX IF NOT EXISTS run_contents_owner_kind_sequence
ON run_contents(run_id, kind, sequence DESC);

CREATE INDEX IF NOT EXISTS run_contents_owner_sequence
ON run_contents(run_id, sequence DESC);

CREATE TABLE IF NOT EXISTS run_repository_refs (
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    ref TEXT NOT NULL,
    digest TEXT NOT NULL,
    PRIMARY KEY (run_id, ref)
);


CREATE TABLE IF NOT EXISTS samples (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    created_at TEXT NOT NULL,
    active_revision INTEGER NOT NULL CHECK (active_revision >= 1),
    record_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sample_revisions (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_id TEXT NOT NULL REFERENCES samples(sample_id) ON DELETE CASCADE,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    content_hash TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    revision_json TEXT NOT NULL,
    UNIQUE (sample_id, revision)
);

CREATE INDEX IF NOT EXISTS sample_revisions_sample_sequence
ON sample_revisions(sample_id, sequence DESC);

CREATE TABLE IF NOT EXISTS sample_mutation_operations (
    operation_id TEXT PRIMARY KEY,
    intent_hash TEXT NOT NULL,
    receipt_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_sample_bindings (
    run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    sample_id TEXT NOT NULL REFERENCES samples(sample_id),
    revision INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    context_id TEXT,
    binding_json TEXT NOT NULL,
    PRIMARY KEY (run_id, role),
    UNIQUE (run_id, sample_id),
    FOREIGN KEY (sample_id, revision)
        REFERENCES sample_revisions(sample_id, revision)
);

CREATE INDEX IF NOT EXISTS run_sample_bindings_sample_run
ON run_sample_bindings(sample_id, run_id);


CREATE TABLE IF NOT EXISTS analysis_publications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_kind TEXT NOT NULL CHECK (subject_kind IN ('run', 'project', 'sample')),
    run_id TEXT REFERENCES runs(run_id) ON DELETE CASCADE,
    sample_id TEXT REFERENCES samples(sample_id),
    subject_json TEXT NOT NULL,
    record_id TEXT NOT NULL,
    record_entry_json TEXT NOT NULL,
    analysis_key TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    publication_hash TEXT NOT NULL,
    published_at TEXT NOT NULL,
    title TEXT NOT NULL,
    step_id TEXT,
    input_count INTEGER NOT NULL CHECK (input_count >= 0),
    output_count INTEGER NOT NULL CHECK (output_count >= 0),
    CHECK (
        (subject_kind = 'run' AND run_id IS NOT NULL AND sample_id IS NULL)
        OR (subject_kind = 'project' AND run_id IS NULL AND sample_id IS NULL)
        OR (subject_kind = 'sample' AND run_id IS NULL AND sample_id IS NOT NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS analysis_publications_project_record
ON analysis_publications(record_id)
WHERE subject_kind = 'project';

CREATE UNIQUE INDEX IF NOT EXISTS analysis_publications_project_key_revision
ON analysis_publications(analysis_key, revision)
WHERE subject_kind = 'project';

CREATE UNIQUE INDEX IF NOT EXISTS analysis_publications_run_record
ON analysis_publications(run_id, record_id)
WHERE subject_kind = 'run';

CREATE UNIQUE INDEX IF NOT EXISTS analysis_publications_run_key_revision
ON analysis_publications(run_id, analysis_key, revision)
WHERE subject_kind = 'run';

CREATE UNIQUE INDEX IF NOT EXISTS analysis_publications_sample_record
ON analysis_publications(sample_id, record_id)
WHERE subject_kind = 'sample';

CREATE UNIQUE INDEX IF NOT EXISTS analysis_publications_sample_key_revision
ON analysis_publications(sample_id, analysis_key, revision)
WHERE subject_kind = 'sample';

CREATE UNIQUE INDEX IF NOT EXISTS analysis_publications_owned_record
ON analysis_publications(record_id)
WHERE subject_kind IN ('project', 'sample');

CREATE INDEX IF NOT EXISTS analysis_publications_project_sequence
ON analysis_publications(sequence DESC)
WHERE subject_kind = 'project';

CREATE INDEX IF NOT EXISTS analysis_publications_run_sequence
ON analysis_publications(run_id, sequence DESC)
WHERE subject_kind = 'run';

CREATE INDEX IF NOT EXISTS analysis_publications_sample_sequence
ON analysis_publications(sample_id, sequence DESC)
WHERE subject_kind = 'sample';

CREATE TABLE IF NOT EXISTS project_analysis_contents (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    publication_sequence INTEGER NOT NULL REFERENCES analysis_publications(sequence)
        ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('artifact', 'dataset', 'record')),
    content_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    produced_by TEXT,
    entry_json TEXT NOT NULL,
    UNIQUE (publication_sequence, content_id)
);

CREATE INDEX IF NOT EXISTS project_analysis_contents_publication_sequence
ON project_analysis_contents(publication_sequence, sequence DESC);

CREATE TABLE IF NOT EXISTS project_analysis_repository_refs (
    publication_sequence INTEGER NOT NULL REFERENCES analysis_publications(sequence)
        ON DELETE CASCADE,
    ref TEXT NOT NULL,
    digest TEXT NOT NULL,
    PRIMARY KEY (publication_sequence, ref)
);


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

CREATE TABLE IF NOT EXISTS procedure_step_attempts (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    procedure_run_id TEXT NOT NULL
        REFERENCES procedure_runs(procedure_run_id) ON DELETE CASCADE,
    step_key TEXT NOT NULL,
    attempt INTEGER NOT NULL CHECK (attempt >= 1),
    operation TEXT NOT NULL CHECK (
        operation IN (
            'run', 'analysis', 'config_activation', 'config_publish',
            'interpretation'
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
    result_registry_generation INTEGER NOT NULL CHECK (
        result_registry_generation >= 1
    ) REFERENCES config_registry_activations(generation) ON DELETE RESTRICT,
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
    base_generation INTEGER NOT NULL CHECK (base_generation >= 1),
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
    superseded_by_generation INTEGER CHECK (
        superseded_by_generation IS NULL OR superseded_by_generation >= 1
    ),
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
            AND superseded_by_generation IS NULL
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
            AND superseded_by_generation IS NULL
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
            AND superseded_by_generation IS NULL
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
            AND superseded_by_generation IS NULL
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
            AND superseded_by_generation IS NOT NULL
            AND superseded_by_generation > base_generation
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
            AND superseded_by_generation IS NULL
            AND superseded_at IS NULL
            AND publication_operation_id IS NOT NULL
            AND published_at IS NOT NULL
        )
    )
);

CREATE INDEX IF NOT EXISTS calibration_cohort_finalizations_state
ON calibration_cohort_finalizations(state, base_generation);

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


CREATE TABLE IF NOT EXISTS procedure_schedules (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_id TEXT NOT NULL UNIQUE,
    intent_hash TEXT NOT NULL,
    due_at TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    state TEXT NOT NULL CHECK (
        state IN ('pending', 'materialized', 'cancelled')
    ),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    procedure_run_id TEXT UNIQUE
        REFERENCES procedure_runs(procedure_run_id),
    schedule_json TEXT NOT NULL,
    CHECK (
        (state = 'materialized' AND procedure_run_id IS NOT NULL)
        OR (state <> 'materialized' AND procedure_run_id IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS procedure_schedules_state_due_sequence
ON procedure_schedules(state, due_at, sequence);

CREATE INDEX IF NOT EXISTS procedure_schedules_state_sequence
ON procedure_schedules(state, sequence);


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


CREATE TABLE IF NOT EXISTS execution_coverage (
    run_id TEXT PRIMARY KEY REFERENCES scheduler_runs(run_id) ON DELETE CASCADE,
    completed_point_count INTEGER NOT NULL CHECK (completed_point_count >= 0)
);

CREATE TABLE IF NOT EXISTS execution_recovery_groups (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES scheduler_runs(run_id) ON DELETE CASCADE,
    segment_id TEXT NOT NULL
        REFERENCES run_execution_segments(segment_id) ON DELETE CASCADE,
    operation_id TEXT NOT NULL,
    schedule_fingerprint TEXT NOT NULL,
    group_id TEXT NOT NULL,
    completion_fingerprint TEXT NOT NULL,
    output_kind TEXT NOT NULL CHECK (
        output_kind IN ('unrecorded', 'measurement')
    ),
    UNIQUE (run_id, group_id),
    UNIQUE (run_id, operation_id)
);

CREATE INDEX IF NOT EXISTS execution_recovery_groups_run_sequence
ON execution_recovery_groups(run_id, sequence);

CREATE TABLE IF NOT EXISTS execution_recovery_group_points (
    run_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    member_index INTEGER NOT NULL CHECK (member_index >= 0),
    point_index INTEGER NOT NULL CHECK (point_index >= 0),
    record_content_hash TEXT,
    PRIMARY KEY (run_id, point_index),
    UNIQUE (run_id, group_id, member_index),
    FOREIGN KEY (run_id, group_id)
        REFERENCES execution_recovery_groups(run_id, group_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS execution_domain_job_transitions (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES scheduler_runs(run_id) ON DELETE CASCADE,
    logical_compute_node_id TEXT NOT NULL,
    execution_key TEXT NOT NULL,
    transition_kind TEXT NOT NULL CHECK (
        transition_kind IN ('invocation', 'checkpoint', 'terminal')
    ),
    job_id TEXT,
    revision INTEGER,
    point_ordinals_json TEXT NOT NULL,
    transition_json TEXT NOT NULL,
    CHECK (
        (
            transition_kind = 'checkpoint'
            AND job_id IS NOT NULL
            AND revision IS NOT NULL
            AND revision >= 1
        ) OR (
            transition_kind IN ('invocation', 'terminal')
            AND job_id IS NULL
            AND revision IS NULL
        )
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS execution_domain_job_checkpoint_identity
ON execution_domain_job_transitions(run_id, execution_key, revision)
WHERE transition_kind = 'checkpoint';

CREATE UNIQUE INDEX IF NOT EXISTS execution_domain_job_invocation_identity
ON execution_domain_job_transitions(run_id, execution_key)
WHERE transition_kind = 'invocation';

CREATE UNIQUE INDEX IF NOT EXISTS execution_domain_job_terminal_identity
ON execution_domain_job_transitions(run_id, execution_key)
WHERE transition_kind = 'terminal';

CREATE INDEX IF NOT EXISTS execution_domain_job_transitions_run_sequence
ON execution_domain_job_transitions(run_id, sequence);

CREATE TABLE IF NOT EXISTS execution_point_plans (
    run_id TEXT PRIMARY KEY REFERENCES scheduler_runs(run_id) ON DELETE CASCADE,
    initialize_operation_id TEXT NOT NULL,
    initial_point_count INTEGER NOT NULL CHECK (initial_point_count >= 0),
    accepted_point_count INTEGER NOT NULL CHECK (accepted_point_count >= 0),
    point_limit INTEGER NOT NULL CHECK (point_limit >= 0),
    plan_closed INTEGER NOT NULL CHECK (plan_closed IN (0, 1)),
    stop_operation_id TEXT,
    stop_reason TEXT,
    CHECK (
        initial_point_count <= accepted_point_count
        AND accepted_point_count <= point_limit
    ),
    CHECK (
        (plan_closed = 1 AND stop_operation_id IS NOT NULL AND stop_reason IS NOT NULL)
        OR (plan_closed = 0 AND stop_operation_id IS NULL AND stop_reason IS NULL)
    )
);

CREATE TABLE IF NOT EXISTS execution_domain_decisions (
    run_id TEXT NOT NULL REFERENCES execution_point_plans(run_id) ON DELETE CASCADE,
    proposal_index INTEGER NOT NULL CHECK (proposal_index >= 0),
    operation_id TEXT NOT NULL,
    decision_json TEXT NOT NULL,
    PRIMARY KEY (run_id, proposal_index),
    UNIQUE (run_id, operation_id)
);

CREATE TABLE IF NOT EXISTS execution_domain_queue (
    run_id TEXT NOT NULL REFERENCES execution_point_plans(run_id) ON DELETE CASCADE,
    queue_index INTEGER NOT NULL CHECK (queue_index >= 0),
    request_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'accepted', 'rejected', 'cancelled')
    ),
    decision_operation_id TEXT,
    entry_json TEXT NOT NULL,
    PRIMARY KEY (run_id, queue_index),
    UNIQUE (run_id, request_id),
    FOREIGN KEY (run_id, decision_operation_id)
        REFERENCES execution_domain_decisions(run_id, operation_id)
);

CREATE TABLE IF NOT EXISTS execution_run_points (
    run_id TEXT NOT NULL REFERENCES execution_point_plans(run_id) ON DELETE CASCADE,
    point_index INTEGER NOT NULL CHECK (point_index >= 0),
    decision_operation_id TEXT NOT NULL,
    point_json TEXT NOT NULL,
    PRIMARY KEY (run_id, point_index),
    FOREIGN KEY (run_id, decision_operation_id)
        REFERENCES execution_domain_decisions(run_id, operation_id)
);

CREATE TABLE IF NOT EXISTS execution_measurement_headers (
    run_id TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL UNIQUE,
    content_hash TEXT NOT NULL,
    contract_fingerprint TEXT NOT NULL,
    expected_record_count INTEGER CHECK (expected_record_count >= 0),
    record_count_limit INTEGER NOT NULL CHECK (record_count_limit >= 0),
    ref TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS execution_measurement_fragments (
    segment_id TEXT PRIMARY KEY
        REFERENCES run_execution_segments(segment_id) ON DELETE CASCADE,
    run_id TEXT NOT NULL,
    header_content_hash TEXT NOT NULL,
    acquisition_start INTEGER NOT NULL CHECK (acquisition_start >= 0),
    FOREIGN KEY (run_id) REFERENCES execution_measurement_headers(run_id)
);

CREATE INDEX IF NOT EXISTS execution_measurement_fragments_run_start
ON execution_measurement_fragments(run_id, acquisition_start);

CREATE TABLE IF NOT EXISTS execution_measurement_appends (
    run_id TEXT NOT NULL,
    segment_id TEXT NOT NULL
        REFERENCES execution_measurement_fragments(segment_id),
    acquisition_start INTEGER NOT NULL CHECK (acquisition_start >= 0),
    operation_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    header_content_hash TEXT NOT NULL,
    record_content_hashes_json TEXT NOT NULL,
    record_count INTEGER NOT NULL CHECK (record_count > 0),
    ref TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES execution_measurement_headers(run_id),
    PRIMARY KEY (run_id, acquisition_start),
    UNIQUE (run_id, operation_id)
);

CREATE TABLE IF NOT EXISTS execution_measurement_records (
    run_id TEXT NOT NULL,
    acquisition_index INTEGER NOT NULL CHECK (acquisition_index >= 0),
    acquisition_start INTEGER NOT NULL CHECK (acquisition_start >= 0),
    row_offset INTEGER NOT NULL CHECK (row_offset >= 0),
    point_index INTEGER NOT NULL CHECK (point_index >= 0),
    record_content_hash TEXT NOT NULL,
    PRIMARY KEY (run_id, acquisition_index),
    UNIQUE (run_id, acquisition_start, row_offset),
    FOREIGN KEY (run_id, acquisition_start)
        REFERENCES execution_measurement_appends(run_id, acquisition_start)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS execution_measurement_records_run_point
ON execution_measurement_records(run_id, point_index, acquisition_index);

CREATE TABLE IF NOT EXISTS execution_measurement_projection (
    run_id TEXT NOT NULL,
    point_index INTEGER NOT NULL CHECK (point_index >= 0),
    projection_index INTEGER NOT NULL CHECK (projection_index >= 0),
    acquisition_index INTEGER NOT NULL CHECK (acquisition_index >= 0),
    group_id TEXT,
    PRIMARY KEY (run_id, point_index),
    UNIQUE (run_id, projection_index),
    UNIQUE (run_id, acquisition_index),
    FOREIGN KEY (run_id, acquisition_index)
        REFERENCES execution_measurement_records(run_id, acquisition_index),
    FOREIGN KEY (run_id, group_id)
        REFERENCES execution_recovery_groups(run_id, group_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS execution_measurement_seals (
    run_id TEXT PRIMARY KEY,
    segment_id TEXT NOT NULL
        REFERENCES execution_measurement_fragments(segment_id),
    operation_id TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    dataset_content_hash TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES execution_measurement_headers(run_id),
    UNIQUE (run_id, operation_id)
);



CREATE TABLE IF NOT EXISTS author_revisions (
    content_hash TEXT PRIMARY KEY,
    bundle_digest TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS author_revision_active (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    generation INTEGER NOT NULL,
    content_hash TEXT NOT NULL REFERENCES author_revisions(content_hash)
);
CREATE TABLE IF NOT EXISTS author_preparations (
    operation_id TEXT PRIMARY KEY,
    record_json TEXT NOT NULL
);


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

COMMIT;
