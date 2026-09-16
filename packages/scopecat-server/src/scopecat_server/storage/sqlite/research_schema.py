"""Research associations do not own or rewrite scientific records."""

RESEARCH_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS research_projects (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL UNIQUE,
    revision INTEGER NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research_samples (
    project_id TEXT NOT NULL REFERENCES research_projects(project_id),
    sample_id TEXT NOT NULL REFERENCES samples(sample_id),
    PRIMARY KEY (project_id, sample_id)
);
CREATE TABLE IF NOT EXISTS research_runs (
    project_id TEXT NOT NULL REFERENCES research_projects(project_id),
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    PRIMARY KEY (project_id, run_id)
);
CREATE INDEX IF NOT EXISTS research_runs_run ON research_runs(run_id);
CREATE TABLE IF NOT EXISTS run_deployments (
    run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
    deployment_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS run_deployments_deployment
ON run_deployments(deployment_id, run_id);
"""
