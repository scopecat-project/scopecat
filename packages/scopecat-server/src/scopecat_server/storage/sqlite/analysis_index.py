"""Shared relational index operations for run and project analyses."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import cast

from pydantic import TypeAdapter
from scopecat.analysis.repository import (
    AnalysisPublication,
    AnalysisPublicationPage,
    AnalysisPublicationSummary,
)
from scopecat.records.analysis import (
    AnalysisRecord,
    AnalysisSubject,
    ConfigurationAnalysisRecordInput,
    MeasurementAnalysisRecordInput,
    ProjectAnalysisSubject,
    RunAnalysisSubject,
    SampleAnalysisSubject,
)
from scopecat.records.content import ContentEntry


class AnalysisIndexConflict(ValueError):
    """An immutable publication identity already has different metadata."""


_ANALYSIS_SUBJECT: TypeAdapter[AnalysisSubject] = TypeAdapter(AnalysisSubject)


def insert_publication(
    connection: sqlite3.Connection,
    publication: AnalysisPublication,
) -> tuple[int, bool]:
    """Insert one immutable index row and return its sequence and creation state."""

    subject_kind, run_id, sample_id = _owner(publication)
    owner_clause, owner_parameters = _owner_clause(publication.subject)
    existing = cast(
        "sqlite3.Row | None",
        connection.execute(
            f"""
            SELECT sequence, subject_json, record_entry_json, analysis_key, revision,
                   publication_hash, title, step_id, input_count, output_count
            FROM analysis_publications
            WHERE {owner_clause} AND record_id = ?
            """,  # noqa: S608 - owner clause is a fixed internal fragment
            (*owner_parameters, publication.record.id),
        ).fetchone(),
    )
    expected = _publication_values(publication)
    if existing is not None:
        actual = tuple(existing[key] for key in _PUBLICATION_VALUE_KEYS)
        if actual != expected:
            raise AnalysisIndexConflict(publication.record.id)
        return cast("int", existing["sequence"]), False

    (
        subject_json,
        record_entry_json,
        analysis_key,
        revision,
        publication_hash,
        title,
        step_id,
        input_count,
        output_count,
    ) = expected
    inserted = connection.execute(
        """
        INSERT INTO analysis_publications(
            subject_kind, run_id, sample_id, subject_json, record_id,
            record_entry_json, analysis_key,
            revision, publication_hash, published_at, title, step_id, input_count,
            output_count
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            subject_kind,
            run_id,
            sample_id,
            subject_json,
            publication.record.id,
            record_entry_json,
            analysis_key,
            revision,
            publication_hash,
            datetime.now(UTC).isoformat(),
            title,
            step_id,
            input_count,
            output_count,
        ),
    )
    sequence = cast("int", inserted.lastrowid)
    run_ids = {
        item.run_id
        for model in publication.models
        if isinstance(model.value, AnalysisRecord)
        for item in model.value.inputs
        if isinstance(
            item, MeasurementAnalysisRecordInput | ConfigurationAnalysisRecordInput
        )
    }
    connection.executemany(
        "INSERT INTO analysis_input_runs(publication_sequence, run_id) VALUES (?, ?)",
        ((sequence, run_id) for run_id in sorted(run_ids)),
    )
    return sequence, True


def list_publications(
    connection: sqlite3.Connection,
    *,
    subject: AnalysisSubject | None,
    limit: int,
    before: int | None,
    input_run_id: str | None = None,
    include_owned: bool = False,
) -> AnalysisPublicationPage:
    clauses: list[str] = []
    parameters: list[str | int] = []
    if subject is not None:
        owner_clause, owner_parameters = _owner_clause(subject)
        clauses.append(owner_clause)
        parameters.extend(owner_parameters)
    if input_run_id is not None:
        input_clause = (
            "sequence IN (SELECT publication_sequence FROM analysis_input_runs "
            "WHERE run_id = ?)"
        )
        parameters.append(input_run_id)
        if include_owned:
            owner_clause, owner_parameters = _owner_clause(
                RunAnalysisSubject(run_id=input_run_id)
            )
            input_clause = f"({input_clause} OR {owner_clause})"
            parameters.extend(owner_parameters)
        clauses.append(input_clause)
    if not clauses:
        raise ValueError("publication listing requires a subject or direct input")
    if before is not None:
        clauses.append("sequence < ?")
        parameters.append(before)
    parameters.append(limit + 1)
    rows = cast(
        "list[sqlite3.Row]",
        connection.execute(
            f"""
            SELECT sequence, subject_json, record_entry_json, title,
                   analysis_key, revision, publication_hash, published_at, step_id,
                   input_count, output_count
            FROM analysis_publications
            WHERE {" AND ".join(clauses)}
            ORDER BY sequence DESC
            LIMIT ?
            """,  # noqa: S608 - clauses are fixed internal SQL fragments
            parameters,
        ).fetchall(),
    )
    selected = rows[:limit]
    return AnalysisPublicationPage(
        items=tuple(summary_from_row(row) for row in selected),
        next_cursor=(
            cast("int", selected[-1]["sequence"]) if len(rows) > limit else None
        ),
    )


def read_publication(
    connection: sqlite3.Connection,
    *,
    subject: AnalysisSubject,
    record_id: str,
) -> AnalysisPublicationSummary | None:
    owner_clause, owner_parameters = _owner_clause(subject)
    parameters: list[str] = list(owner_parameters)
    parameters.append(record_id)
    row = cast(
        "sqlite3.Row | None",
        connection.execute(
            f"""
            SELECT subject_json, record_entry_json, title, analysis_key,
                   revision, publication_hash, published_at, step_id, input_count,
                   output_count
            FROM analysis_publications
            WHERE {owner_clause} AND record_id = ?
            """,  # noqa: S608 - owner clause is a fixed internal SQL fragment
            parameters,
        ).fetchone(),
    )
    return None if row is None else summary_from_row(row)


def latest_publication(
    connection: sqlite3.Connection,
    *,
    subject: AnalysisSubject,
    analysis_key: str,
) -> AnalysisPublicationSummary | None:
    owner_clause, owner_parameters = _owner_clause(subject)
    parameters: list[str] = list(owner_parameters)
    parameters.append(analysis_key)
    row = cast(
        "sqlite3.Row | None",
        connection.execute(
            f"""
            SELECT subject_json, record_entry_json, title, analysis_key,
                   revision, publication_hash, published_at, step_id, input_count,
                   output_count
            FROM analysis_publications
            WHERE {owner_clause} AND analysis_key = ?
            ORDER BY revision DESC
            LIMIT 1
            """,  # noqa: S608 - owner clause is a fixed internal SQL fragment
            parameters,
        ).fetchone(),
    )
    return None if row is None else summary_from_row(row)


def summary_from_row(row: sqlite3.Row) -> AnalysisPublicationSummary:
    subject = _ANALYSIS_SUBJECT.validate_json(cast("str", row["subject_json"]))
    return AnalysisPublicationSummary(
        subject=subject,
        record=ContentEntry.model_validate_json(cast("str", row["record_entry_json"])),
        title=cast("str", row["title"]),
        analysis_key=cast("str", row["analysis_key"]),
        revision=cast("int", row["revision"]),
        publication_hash=cast("str", row["publication_hash"]),
        published_at=datetime.fromisoformat(cast("str", row["published_at"])),
        step_id=cast("str | None", row["step_id"]),
        input_count=cast("int", row["input_count"]),
        output_count=cast("int", row["output_count"]),
    )


_PUBLICATION_VALUE_KEYS = (
    "subject_json",
    "record_entry_json",
    "analysis_key",
    "revision",
    "publication_hash",
    "title",
    "step_id",
    "input_count",
    "output_count",
)


def _publication_values(publication: AnalysisPublication) -> tuple[object, ...]:
    return (
        publication.subject.model_dump_json(),
        publication.record.model_dump_json(),
        publication.analysis_key,
        publication.revision,
        publication.publication_hash,
        publication.title,
        publication.step_id,
        publication.input_count,
        publication.output_count,
    )


def _owner(
    publication: AnalysisPublication,
) -> tuple[str, str | None, str | None]:
    subject = publication.subject
    if isinstance(subject, RunAnalysisSubject):
        return "run", subject.run_id, None
    if isinstance(subject, SampleAnalysisSubject):
        return "sample", None, subject.sample_id
    return "project", None, None


def _owner_clause(subject: AnalysisSubject) -> tuple[str, tuple[str, ...]]:
    if isinstance(subject, RunAnalysisSubject):
        return "subject_kind = 'run' AND run_id = ?", (subject.run_id,)
    if isinstance(subject, SampleAnalysisSubject):
        return "subject_kind = 'sample' AND sample_id = ?", (subject.sample_id,)
    assert isinstance(subject, ProjectAnalysisSubject)
    return "subject_kind = 'project'", ()


__all__ = [
    "AnalysisIndexConflict",
    "insert_publication",
    "latest_publication",
    "list_publications",
    "read_publication",
]
