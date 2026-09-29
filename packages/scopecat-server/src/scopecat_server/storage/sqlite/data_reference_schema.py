"""Scientific JSON owners used by both retention checks and commit fences."""

DATA_REFERENCE_COLUMNS = (
    ("runs", "run_id", "config_source_json", "run"),
    ("runs", "run_id", "execution_setup_json", "run"),
    ("scheduler_runs", "run_id", "admission_json", "run"),
    ("procedure_runs", "procedure_run_id", "run_json", "procedure"),
    ("procedure_step_attempts", "procedure_run_id", "attempt_json", "procedure"),
    ("calibration_tasks", "task_id", "record_json", "calibration_task"),
    ("calibration_profiles", "profile_id", "record_json", "calibration_profile"),
    ("parameter_revisions", "revision_id", "record_json", "parameters"),
    ("parameter_branch_commits", "name", "record_json", "branch"),
    ("setup_revisions", "revision_id", "record_json", "setup"),
    ("setup_definitions", "definition_id", "record_json", "setup_definition"),
    ("config_registry_entries", "entry_id", "entry_json", "configuration"),
    ("config_registry_entries", "entry_id", "parameters_json", "configuration"),
    ("sample_revisions", "sample_id", "revision_json", "sample"),
    ("measurement_target_revisions", "target_id", "revision_json", "target"),
    ("apparatus_object_revisions", "object_id", "revision_json", "apparatus"),
    ("apparatus_observations", "observation_id", "observation_json", "observation"),
    ("config_registry_activations", "generation", "record_json", "activation"),
    ("procedure_schedules", "schedule_id", "schedule_json", "schedule"),
)
