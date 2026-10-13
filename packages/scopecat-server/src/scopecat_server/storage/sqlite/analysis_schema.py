"""SQLite analysis publication index and project-owned content tables."""

ANALYSIS_TABLES_SQL = """
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

CREATE UNIQUE INDEX IF NOT EXISTS analysis_publications_identity
ON analysis_publications(record_id);

CREATE TABLE IF NOT EXISTS analysis_input_runs (
    publication_sequence INTEGER NOT NULL REFERENCES analysis_publications(sequence)
        ON DELETE CASCADE,
    run_id TEXT NOT NULL,
    PRIMARY KEY (publication_sequence, run_id)
);
CREATE INDEX IF NOT EXISTS analysis_input_runs_lookup
ON analysis_input_runs(run_id, publication_sequence DESC);

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
"""
