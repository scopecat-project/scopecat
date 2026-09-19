"""SQLite metadata index backed by immutable content-addressed objects."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import cast

from pydantic import BaseModel, TypeAdapter, ValidationError
from pydantic_core import PydanticSerializationError
from scopecat.analysis.repository import (
    AnalysisPublication,
    AnalysisPublicationPage,
    AnalysisPublicationSummary,
)
from scopecat.kernel.errors import (
    CheckFailed,
    Conflict,
    DataIntegrityError,
    NotFound,
    StorageError,
)
from scopecat.kernel.problems import (
    ModelLocation,
    ProblemPhase,
    StorageLocation,
    problem,
)
from scopecat.kernel.run_outcome import RunOutcome
from scopecat.records.analysis import RunAnalysisSubject
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.content import ContentEntry
from scopecat.records.measurement import MeasurementRecord
from scopecat.records.measurement_recording import MeasurementDatasetHeader
from scopecat.records.run import RunConfigSource, RunSnapshot
from scopecat.records.sample import SampleBinding
from scopecat.records.scientific_binding import ResolvedScientificBinding
from scopecat.runs.admission import RunSkeleton
from scopecat.runs.provenance import validate_run_config_provenance
from scopecat.runs.refs import (
    CONFIG_PROFILE_SNAPSHOT_REF,
    RUN_REQUEST_REF,
    SCIENTIFIC_BINDING_REF,
)
from scopecat.runs.repository import (
    RunContentPage,
    RunContentPublication,
    RunContentRole,
    TerminalRunCommit,
)

from scopecat_server.storage.sqlite.analysis_index import (
    AnalysisIndexConflict,
    insert_publication,
    latest_publication,
    list_publications,
    read_publication,
)
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.object_store import (
    ImmutableObjectStore,
    ObjectCorruptError,
    ObjectNotFoundError,
    ObjectStoreError,
    StoredObject,
)

_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_RUN_CONFIG_SOURCE: TypeAdapter[RunConfigSource] = TypeAdapter(RunConfigSource)


@dataclass(frozen=True, slots=True)
class _PreparedRef:
    ref: str
    object: StoredObject
    replace: bool = True


@dataclass(frozen=True, slots=True)
class PreparedTerminalCommit:
    """Immutable terminal objects prepared before the database write lock."""

    commit: TerminalRunCommit
    refs: tuple[_PreparedRef, ...]


@dataclass(frozen=True, slots=True)
class PreparedRunSkeleton:
    """Immutable admission objects prepared before the database write lock."""

    snapshot: RunSnapshot
    refs: tuple[_PreparedRef, ...]


@dataclass(frozen=True, slots=True)
class PreparedContentPublication:
    """Immutable content objects prepared before metadata publication."""

    publication: RunContentPublication
    refs: tuple[_PreparedRef, ...]


@dataclass(frozen=True, slots=True)
class PreparedRunAnalysisPublication:
    """Run analysis index and content prepared for one atomic publication."""

    publication: AnalysisPublication
    content: PreparedContentPublication


class SQLiteRunRepository:
    """Relational run metadata with payloads in a SHA-256 object directory."""

    @staticmethod
    def deployment_in_transaction(
        connection: sqlite3.Connection, run_id: str
    ) -> str | None:
        row = cast(
            "sqlite3.Row | None",
            connection.execute(
                "SELECT deployment_id FROM run_deployments WHERE run_id=?", (run_id,)
            ).fetchone(),
        )
        return cast("str", row[0]) if row is not None else None

    def __init__(
        self,
        database: SQLiteDatabase,
        objects: str | Path,
    ) -> None:
        self.sqlite = database
        self.database = database.path
        self.objects = ImmutableObjectStore(objects)

    def exists(self, run_id: str, ref: str) -> bool:
        _validate_identity(run_id, ref)
        prefix = f"{ref}/"
        try:
            with self.sqlite.read_connection() as connection:
                row = _one(
                    connection.execute(
                        """
                        SELECT 1 AS present FROM run_repository_refs
                        WHERE run_id = ?
                          AND (
                              ref = ?
                              OR substr(ref, 1, ?) = ?
                          )
                        LIMIT 1
                        """,
                        (run_id, ref, len(prefix), prefix),
                    )
                )
        except sqlite3.Error as error:
            raise _storage_failure(run_id=run_id, ref=ref) from error
        return row is not None

    def read_snapshot(self, run_id: str) -> RunSnapshot:
        _validate_run_id(run_id)
        try:
            with self.sqlite.read_connection() as connection:
                return self.read_snapshot_in_transaction(connection, run_id)
        except sqlite3.Error as error:
            raise _storage_failure(run_id=run_id) from error

    def read_snapshot_in_transaction(
        self,
        connection: sqlite3.Connection,
        run_id: str,
    ) -> RunSnapshot:
        _validate_run_id(run_id)
        return self._read_snapshot_with_connection(connection, run_id)

    def list_contents(
        self,
        run_id: str,
        *,
        limit: int,
        before: int | None = None,
        role: RunContentRole | None = None,
        kind: str | None = None,
    ) -> RunContentPage:
        _validate_run_id(run_id)
        try:
            with self.sqlite.read_connection() as connection:
                self._require_run_row(connection, run_id)
                clauses = ["run_id = ?"]
                parameters: list[str | int] = [run_id]
                if before is not None:
                    clauses.append("sequence < ?")
                    parameters.append(before)
                if role is not None:
                    clauses.append("role = ?")
                    parameters.append(role)
                if kind is not None:
                    clauses.append("kind = ?")
                    parameters.append(kind)
                parameters.append(limit + 1)
                rows = _all(
                    connection.execute(
                        f"""
                        SELECT sequence, entry_json
                        FROM run_contents
                        WHERE {" AND ".join(clauses)}
                        ORDER BY sequence DESC
                        LIMIT ?
                        """,  # noqa: S608 - clauses are fixed internal fragments
                        parameters,
                    )
                )
            selected = rows[:limit]
            return RunContentPage(
                items=tuple(
                    ContentEntry.model_validate_json(_text(row, "entry_json"))
                    for row in selected
                ),
                next_cursor=(
                    cast("int", selected[-1]["sequence"]) if len(rows) > limit else None
                ),
            )
        except NotFound:
            raise
        except (sqlite3.Error, ValidationError) as error:
            raise _storage_failure(run_id=run_id) from error

    def read_content(
        self,
        run_id: str,
        *,
        role: RunContentRole,
        content_id: str,
    ) -> ContentEntry:
        _validate_run_id(run_id)
        try:
            with self.sqlite.read_connection() as connection:
                return self.read_content_in_transaction(
                    connection,
                    run_id,
                    role=role,
                    content_id=content_id,
                )
        except NotFound, DataIntegrityError:
            raise
        except (sqlite3.Error, ValidationError) as error:
            raise _storage_failure(run_id=run_id) from error

    def read_content_in_transaction(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        *,
        role: RunContentRole,
        content_id: str,
    ) -> ContentEntry:
        self._require_run_row(connection, run_id)
        row = _one(
            connection.execute(
                """
                SELECT entry_json FROM run_contents
                WHERE run_id = ? AND role = ? AND content_id = ?
                """,
                (run_id, role, content_id),
            )
        )
        if row is None:
            raise _content_not_found(run_id, role, content_id)
        try:
            return ContentEntry.model_validate_json(_text(row, "entry_json"))
        except ValidationError as error:
            raise _integrity_failure(
                run_id=run_id,
                code="run.content_invalid",
                message="run content metadata does not match its durable schema",
            ) from error

    def list_analysis_publications(
        self,
        run_id: str,
        *,
        limit: int,
        before: int | None = None,
    ) -> AnalysisPublicationPage:
        _validate_run_id(run_id)
        try:
            with self.sqlite.read_connection() as connection:
                self._require_run_row(connection, run_id)
                return list_publications(
                    connection,
                    subject=RunAnalysisSubject(run_id=run_id),
                    limit=limit,
                    before=before,
                )
        except NotFound:
            raise
        except (sqlite3.Error, ValidationError) as error:
            raise _storage_failure(run_id=run_id) from error

    def read_analysis_publication(
        self,
        run_id: str,
        record_id: str,
    ) -> AnalysisPublicationSummary:
        _validate_run_id(run_id)
        try:
            with self.sqlite.read_connection() as connection:
                self._require_run_row(connection, run_id)
                publication = read_publication(
                    connection,
                    subject=RunAnalysisSubject(run_id=run_id),
                    record_id=record_id,
                )
            if publication is None:
                raise _analysis_not_found(run_id, record_id)
            return publication
        except NotFound:
            raise
        except (sqlite3.Error, ValidationError) as error:
            raise _storage_failure(run_id=run_id) from error

    def latest_analysis_publication(
        self,
        run_id: str,
        analysis_key: str,
    ) -> AnalysisPublicationSummary | None:
        _validate_run_id(run_id)
        try:
            with self.sqlite.read_connection() as connection:
                self._require_run_row(connection, run_id)
                return latest_publication(
                    connection,
                    subject=RunAnalysisSubject(run_id=run_id),
                    analysis_key=analysis_key,
                )
        except NotFound:
            raise
        except (sqlite3.Error, ValidationError) as error:
            raise _storage_failure(run_id=run_id) from error

    def prepare_run_skeleton(self, skeleton: RunSkeleton) -> PreparedRunSkeleton:
        """Write immutable admission objects before acquiring the SQLite writer."""

        snapshot = skeleton.snapshot
        run_id = snapshot.run_id
        prepared = [
            self._prepare_model(
                run_id,
                CONFIG_PROFILE_SNAPSHOT_REF,
                skeleton.config,
            ),
            self._prepare_model(run_id, RUN_REQUEST_REF, skeleton.request),
            self._prepare_model(
                run_id, SCIENTIFIC_BINDING_REF, snapshot.scientific_binding
            ),
        ]
        return PreparedRunSkeleton(
            snapshot=snapshot,
            refs=tuple(prepared),
        )

    def commit_run_skeleton_in_transaction(
        self,
        connection: sqlite3.Connection,
        prepared: PreparedRunSkeleton,
    ) -> None:
        """Publish a prebuilt run skeleton in the caller's transaction."""

        self._replace_run_snapshot(connection, prepared.snapshot)
        self._publish_refs(connection, prepared.snapshot.run_id, prepared.refs)

    def commit_terminal(self, commit: TerminalRunCommit) -> RunSnapshot:
        run_id = commit.run_id
        prepared = self.prepare_terminal_commit(commit)
        try:
            with self._transaction() as connection:
                return self.commit_prepared_terminal_in_transaction(
                    connection,
                    prepared,
                )
        except sqlite3.Error as error:
            raise _storage_failure(run_id=run_id) from error

    def prepare_terminal_commit(
        self,
        commit: TerminalRunCommit,
    ) -> PreparedTerminalCommit:
        """Write immutable terminal payloads before acquiring the SQLite writer."""

        run_id = commit.run_id
        refs = [
            self._prepare_model(run_id, write.ref, write.value)
            for write in commit.models
        ]
        return PreparedTerminalCommit(commit=commit, refs=tuple(refs))

    def commit_prepared_terminal_in_transaction(
        self,
        connection: sqlite3.Connection,
        prepared: PreparedTerminalCommit,
    ) -> RunSnapshot:
        """Publish prepared terminal refs without managing the transaction.

        The first terminal outcome wins while independently published contents
        merge through relational uniqueness.
        """

        commit = prepared.commit
        run_id = commit.run_id
        self._require_run_row(connection, run_id)
        outcome = commit.outcome
        connection.execute(
            """
            INSERT INTO run_outcomes(
                run_id, result, certainty, finished_at, outcome_json
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO NOTHING
            """,
            (
                run_id,
                outcome.result,
                outcome.certainty,
                outcome.finished_at.isoformat(),
                _encode_model(outcome).decode(),
            ),
        )
        self._upsert_contents(connection, run_id, commit.contents)
        self._publish_refs(connection, run_id, prepared.refs)
        return self._read_snapshot_with_connection(connection, run_id)

    def publish_content(
        self,
        publication: RunContentPublication,
    ) -> None:
        prepared = self.prepare_content_publication(publication)
        try:
            with self._transaction() as connection:
                self.publish_prepared_content_in_transaction(
                    connection,
                    prepared,
                )
        except sqlite3.Error as error:
            raise _storage_failure(run_id=publication.run_id) from error

    def publish_analysis(self, publication: AnalysisPublication) -> None:
        prepared = self.prepare_analysis_publication(publication)
        run_id = _analysis_run_id(publication)
        try:
            with self._transaction() as connection:
                self.publish_prepared_analysis_in_transaction(connection, prepared)
        except Conflict, NotFound, DataIntegrityError:
            raise
        except sqlite3.Error as error:
            raise _storage_failure(run_id=run_id) from error

    def prepare_analysis_publication(
        self,
        publication: AnalysisPublication,
    ) -> PreparedRunAnalysisPublication:
        run_id = _analysis_run_id(publication)
        return PreparedRunAnalysisPublication(
            publication=publication,
            content=self.prepare_content_publication(
                RunContentPublication(
                    run_id=run_id,
                    entries=publication.entries,
                    models=publication.models,
                    bytes=publication.bytes,
                )
            ),
        )

    def publish_prepared_analysis_in_transaction(
        self,
        connection: sqlite3.Connection,
        prepared: PreparedRunAnalysisPublication,
    ) -> bool:
        publication = prepared.publication
        run_id = _analysis_run_id(publication)
        self._require_run_row(connection, run_id)
        self.publish_prepared_content_in_transaction(connection, prepared.content)
        try:
            _sequence, created = insert_publication(connection, publication)
        except AnalysisIndexConflict as error:
            raise _analysis_conflict(run_id, publication.record.id) from error
        except sqlite3.IntegrityError as error:
            raise _analysis_conflict(run_id, publication.record.id) from error
        return created

    def prepare_content_publication(
        self,
        publication: RunContentPublication,
    ) -> PreparedContentPublication:
        """Write immutable objects without publishing their logical refs."""

        run_id = publication.run_id
        refs = [
            replace(
                self._prepare_model(run_id, write.ref, write.value),
                replace=write.replace,
            )
            for write in publication.models
        ]
        refs.extend(
            self._prepare_bytes(
                run_id,
                write.ref,
                write.content,
                replace=write.replace,
            )
            for write in publication.bytes
        )
        return PreparedContentPublication(
            publication=publication,
            refs=tuple(refs),
        )

    def publish_prepared_content_in_transaction(
        self,
        connection: sqlite3.Connection,
        prepared: PreparedContentPublication,
    ) -> None:
        """Publish prepared refs and content metadata in one transaction."""

        publication = prepared.publication
        run_id = publication.run_id
        self._require_run_row(connection, run_id)
        self._upsert_contents(connection, run_id, publication.entries)
        self._publish_refs(connection, run_id, prepared.refs)

    def read_config_profile_snapshot(self, run_id: str) -> ConfigProfileSnapshot:
        snapshot = self.read_snapshot(run_id)
        config = self.read_model(
            run_id,
            CONFIG_PROFILE_SNAPSHOT_REF,
            ConfigProfileSnapshot,
        )
        validate_run_config_provenance(snapshot=snapshot, config=config)
        return config

    def read_model[TModel: BaseModel](
        self,
        run_id: str,
        ref: str,
        model_type: type[TModel],
    ) -> TModel:
        content = self._read_ref(run_id, ref)
        try:
            return model_type.model_validate_json(content)
        except ValidationError as error:
            raise _invalid_ref(run_id, ref) from error

    def read_measurement_records(
        self,
        run_id: str,
        ref: str,
    ) -> list[MeasurementRecord]:
        from scopecat.measurements.recording_arrow import (
            MeasurementArrowCodecError,
            decode_measurement_record_indices,
            measurement_dataset_schema_hash,
        )

        _validate_identity(run_id, ref)
        header = self.read_model(
            run_id,
            f"{ref}/header.json",
            MeasurementDatasetHeader,
        )
        dataset_schema_hash = measurement_dataset_schema_hash(header.dataset_schema)
        try:
            with self.sqlite.read_connection() as connection:
                rows = _all(
                    connection.execute(
                        """
                        SELECT projection.acquisition_index,
                               record.row_offset,
                               append.ref
                        FROM execution_measurement_projection AS projection
                        JOIN execution_measurement_records AS record
                          ON record.run_id = projection.run_id
                         AND record.acquisition_index = projection.acquisition_index
                        JOIN execution_measurement_appends AS append
                          ON append.run_id = record.run_id
                         AND append.acquisition_start = record.acquisition_start
                        WHERE projection.run_id = ?
                        ORDER BY projection.point_index
                        """,
                        (run_id,),
                    )
                )
        except sqlite3.Error as error:
            raise _storage_failure(run_id=run_id, ref=ref) from error
        grouped: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            grouped.setdefault(_text(row, "ref"), []).append(row)
        records_by_acquisition: dict[int, MeasurementRecord] = {}
        for chunk_ref, selected in grouped.items():
            try:
                decoded = decode_measurement_record_indices(
                    self.read_bytes(run_id, chunk_ref),
                    header.dataset_schema,
                    tuple(_integer(row, "row_offset") for row in selected),
                    dataset_schema_hash=dataset_schema_hash,
                )
            except MeasurementArrowCodecError as error:
                raise _invalid_ref(run_id, chunk_ref) from error
            records_by_acquisition.update(
                zip(
                    (_integer(row, "acquisition_index") for row in selected),
                    decoded,
                    strict=True,
                )
            )
        return [
            records_by_acquisition[_integer(row, "acquisition_index")] for row in rows
        ]

    def read_text(self, run_id: str, ref: str) -> str:
        content = self._read_ref(run_id, ref)
        try:
            return content.decode()
        except UnicodeError as error:
            raise _invalid_ref(run_id, ref) from error

    def read_bytes(self, run_id: str, ref: str) -> bytes:
        return self._read_ref(run_id, ref)

    def _prepare_model(
        self,
        run_id: str,
        ref: str,
        model: BaseModel,
    ) -> _PreparedRef:
        _validate_identity(run_id, ref)
        try:
            content = _encode_model(model) + b"\n"
            stored = self.objects.put(content)
        except ObjectStoreError as error:
            raise _storage_failure(run_id=run_id, ref=ref) from error
        except (PydanticSerializationError, TypeError, ValueError) as error:
            raise _serialization_failure(run_id, ref) from error
        return _PreparedRef(ref=ref, object=stored)

    def _prepare_bytes(
        self,
        run_id: str,
        ref: str,
        content: bytes,
        *,
        replace: bool = True,
    ) -> _PreparedRef:
        _validate_identity(run_id, ref)
        try:
            stored = self.objects.put(content)
        except ObjectStoreError as error:
            raise _storage_failure(run_id=run_id, ref=ref) from error
        return _PreparedRef(ref=ref, object=stored, replace=replace)

    @staticmethod
    def _publish_refs(
        connection: sqlite3.Connection,
        run_id: str,
        prepared: Iterable[_PreparedRef],
    ) -> None:
        for item in prepared:
            if item.replace:
                connection.execute(
                    """
                    INSERT INTO run_repository_refs(run_id, ref, digest)
                    VALUES (?, ?, ?)
                    ON CONFLICT(run_id, ref) DO UPDATE SET
                        digest = excluded.digest
                    """,
                    (run_id, item.ref, item.object.digest),
                )
                continue
            inserted = connection.execute(
                """
                INSERT INTO run_repository_refs(run_id, ref, digest)
                VALUES (?, ?, ?)
                ON CONFLICT(run_id, ref) DO NOTHING
                """,
                (run_id, item.ref, item.object.digest),
            )
            if inserted.rowcount == 1:
                continue
            existing = _one(
                connection.execute(
                    """
                    SELECT digest FROM run_repository_refs
                    WHERE run_id = ? AND ref = ?
                    """,
                    (run_id, item.ref),
                )
            )
            if existing is None or _text(existing, "digest") != item.object.digest:
                raise _ref_conflict(run_id, item.ref)

    def _read_ref(self, run_id: str, ref: str) -> bytes:
        _validate_identity(run_id, ref)
        digest = self._digest(run_id, ref)
        if digest is None:
            raise _integrity_failure(
                run_id=run_id,
                ref=ref,
                code="run.ref_missing",
                message="run is missing a referenced durable record",
            )
        return self._read_object(digest, run_id=run_id, ref=ref)

    def _read_snapshot_with_connection(
        self,
        connection: sqlite3.Connection,
        run_id: str,
    ) -> RunSnapshot:
        row = _one(
            connection.execute(
                """
                SELECT runs.*, run_outcomes.outcome_json
                FROM runs
                LEFT JOIN run_outcomes USING (run_id)
                WHERE runs.run_id = ?
                """,
                (run_id,),
            )
        )
        if row is None:
            raise NotFound(
                [
                    problem(
                        "run.not_found",
                        "run was not found",
                        phase=ProblemPhase.PERSISTENCE,
                        location=StorageLocation(run_id=run_id),
                    )
                ]
            )
        try:
            config_source_json = cast("str | None", row["config_source_json"])
            outcome_json = cast("str | None", row["outcome_json"])
            binding_rows = _all(
                connection.execute(
                    """
                    SELECT binding_json
                    FROM run_sample_bindings
                    WHERE run_id = ?
                    ORDER BY role
                    """,
                    (run_id,),
                )
            )
            binding_ref = _one(
                connection.execute(
                    "SELECT digest FROM run_repository_refs WHERE run_id=? AND ref=?",
                    (run_id, SCIENTIFIC_BINDING_REF),
                )
            )
            if binding_ref is None:
                raise _integrity_failure(
                    run_id=run_id,
                    ref=SCIENTIFIC_BINDING_REF,
                    code="run.ref_missing",
                    message="run is missing its scientific binding",
                )
            binding = ResolvedScientificBinding.model_validate_json(
                self._read_object(
                    _text(binding_ref, "digest"),
                    run_id=run_id,
                    ref=SCIENTIFIC_BINDING_REF,
                )
            )
            return RunSnapshot(
                run_id=run_id,
                scientific_binding=binding,
                created_at=datetime.fromisoformat(_text(row, "created_at")),
                config_content_hash=_text(row, "config_content_hash"),
                config_source=(
                    None
                    if config_source_json is None
                    else _RUN_CONFIG_SOURCE.validate_json(config_source_json)
                ),
                outcome=(
                    None
                    if outcome_json is None
                    else RunOutcome.model_validate_json(outcome_json)
                ),
                samples=tuple(
                    SampleBinding.model_validate_json(_text(item, "binding_json"))
                    for item in binding_rows
                ),
            )
        except ValidationError as error:
            raise _integrity_failure(
                run_id=run_id,
                code="run.snapshot_invalid",
                message="run metadata does not match its durable schema",
            ) from error

    @staticmethod
    def _require_run_row(connection: sqlite3.Connection, run_id: str) -> None:
        row = _one(
            connection.execute(
                "SELECT 1 AS present FROM runs WHERE run_id = ?",
                (run_id,),
            )
        )
        if row is None:
            raise NotFound(
                [
                    problem(
                        "run.not_found",
                        "run was not found",
                        phase=ProblemPhase.PERSISTENCE,
                        location=StorageLocation(run_id=run_id),
                    )
                ]
            )

    @staticmethod
    def _upsert_contents(
        connection: sqlite3.Connection,
        run_id: str,
        entries: Iterable[ContentEntry],
    ) -> None:
        connection.executemany(
            """
            INSERT INTO run_contents(
                run_id, role, content_id, kind, produced_by, entry_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, role, content_id) DO UPDATE SET
                kind = excluded.kind,
                produced_by = excluded.produced_by,
                entry_json = excluded.entry_json
            """,
            (
                (
                    run_id,
                    entry.role,
                    entry.id,
                    entry.kind,
                    entry.produced_by,
                    _encode_model(entry).decode(),
                )
                for entry in entries
            ),
        )

    def _replace_run_snapshot(
        self,
        connection: sqlite3.Connection,
        snapshot: RunSnapshot,
    ) -> None:
        source = snapshot.config_source
        connection.execute(
            """
            INSERT INTO runs(
                run_id, created_at, config_content_hash, config_source_json
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                created_at = excluded.created_at,
                config_content_hash = excluded.config_content_hash,
                config_source_json = excluded.config_source_json
            """,
            (
                snapshot.run_id,
                snapshot.created_at.isoformat(),
                snapshot.config_content_hash,
                None if source is None else _encode_model(source).decode(),
            ),
        )
        connection.execute(
            "DELETE FROM run_outcomes WHERE run_id = ?",
            (snapshot.run_id,),
        )
        if (outcome := snapshot.outcome) is not None:
            connection.execute(
                """
                INSERT INTO run_outcomes(
                    run_id, result, certainty, finished_at, outcome_json
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    snapshot.run_id,
                    outcome.result,
                    outcome.certainty,
                    outcome.finished_at.isoformat(),
                    _encode_model(outcome).decode(),
                ),
            )

    def _digest(self, run_id: str, ref: str) -> str | None:
        try:
            with self.sqlite.read_connection() as connection:
                row = _one(
                    connection.execute(
                        """
                        SELECT digest FROM run_repository_refs
                        WHERE run_id = ? AND ref = ?
                        """,
                        (run_id, ref),
                    )
                )
        except sqlite3.Error as error:
            raise _storage_failure(run_id=run_id, ref=ref) from error
        return None if row is None else _text(row, "digest")

    def _read_object(self, digest: str, *, run_id: str, ref: str) -> bytes:
        try:
            if ref.startswith("data/measurement_dataset/") and "/chunks/" in ref:
                return self.objects.read_cached(digest)
            return self.objects.read(digest)
        except (ObjectNotFoundError, ObjectCorruptError) as error:
            raise _invalid_ref(run_id, ref) from error
        except ObjectStoreError as error:
            raise _storage_failure(run_id=run_id, ref=ref) from error

    @contextmanager
    def _transaction(self) -> Generator[sqlite3.Connection]:
        with self.sqlite.write_transaction() as connection:
            yield connection


def _encode_model(model: BaseModel) -> bytes:
    return json.dumps(
        model.model_dump(mode="json"),
        allow_nan=True,
        separators=(",", ":"),
    ).encode()


def _validate_identity(run_id: str, ref: str) -> None:
    _validate_run_id(run_id)
    relative = PurePosixPath(ref)
    if not relative.parts or relative.is_absolute() or ".." in relative.parts:
        raise CheckFailed(
            [
                problem(
                    "run.ref_path_escape",
                    "run ref must stay within the run directory",
                    phase=ProblemPhase.PERSISTENCE,
                    location=ModelLocation(root="run_ref", path=("ref",)),
                    details={"ref": ref},
                )
            ]
        )


def _validate_run_id(run_id: str) -> None:
    if _SAFE_RUN_ID.fullmatch(run_id):
        return
    raise CheckFailed(
        [
            problem(
                "run.id_invalid",
                "run id is not safe for storage access",
                phase=ProblemPhase.PERSISTENCE,
                location=ModelLocation(root="run", path=("run_id",)),
                details={"run_id": run_id},
            )
        ]
    )


def _serialization_failure(run_id: str, ref: str) -> DataIntegrityError:
    return _integrity_failure(
        run_id=run_id,
        ref=ref,
        code="run.ref_not_serializable",
        message="run record cannot be represented by the durable format",
    )


def _invalid_ref(run_id: str, ref: str) -> DataIntegrityError:
    return _integrity_failure(
        run_id=run_id,
        ref=ref,
        code="run.ref_invalid",
        message="run record does not match its durable schema",
    )


def _ref_conflict(run_id: str, ref: str) -> Conflict:
    return Conflict(
        [
            problem(
                "run.ref_conflict",
                "immutable run content already contains different data",
                phase=ProblemPhase.PERSISTENCE,
                location=StorageLocation(run_id=run_id, ref=ref),
            )
        ]
    )


def _content_not_found(
    run_id: str,
    role: RunContentRole,
    content_id: str,
) -> NotFound:
    return NotFound(
        [
            problem(
                f"run.{role}_not_found",
                f"run {role} was not found",
                phase=ProblemPhase.PERSISTENCE,
                location=StorageLocation(
                    run_id=run_id,
                    path=(role, content_id),
                ),
            )
        ]
    )


def _analysis_not_found(run_id: str, record_id: str) -> NotFound:
    return NotFound(
        [
            problem(
                "run.analysis_not_found",
                "run analysis publication was not found",
                phase=ProblemPhase.PERSISTENCE,
                location=StorageLocation(
                    run_id=run_id,
                    path=("analysis", record_id),
                ),
            )
        ]
    )


def _analysis_conflict(run_id: str, record_id: str) -> Conflict:
    return Conflict(
        [
            problem(
                "run.analysis_conflict",
                "run analysis identity already contains different metadata",
                phase=ProblemPhase.PERSISTENCE,
                location=StorageLocation(
                    run_id=run_id,
                    path=("analysis", record_id),
                ),
            )
        ]
    )


def _analysis_run_id(publication: AnalysisPublication) -> str:
    subject = publication.subject
    if not isinstance(subject, RunAnalysisSubject):
        raise TypeError("run repository requires a run analysis subject")
    return subject.run_id


def _integrity_failure(
    *,
    ref: str | None = None,
    code: str,
    message: str,
    run_id: str | None = None,
) -> DataIntegrityError:
    return DataIntegrityError(
        [
            problem(
                code,
                message,
                phase=ProblemPhase.PERSISTENCE,
                location=StorageLocation(run_id=run_id, ref=ref),
            )
        ]
    )


def _storage_failure(
    *,
    ref: str | None = None,
    run_id: str | None = None,
) -> StorageError:
    return StorageError(
        [
            problem(
                "storage.operation_failed",
                "storage could not complete the run repository operation",
                phase=ProblemPhase.PERSISTENCE,
                location=StorageLocation(run_id=run_id, ref=ref),
            )
        ]
    )


def _one(cursor: sqlite3.Cursor) -> sqlite3.Row | None:
    return cast("sqlite3.Row | None", cursor.fetchone())


def _all(cursor: sqlite3.Cursor) -> list[sqlite3.Row]:
    return cast("list[sqlite3.Row]", cursor.fetchall())


def _text(row: sqlite3.Row, column: str) -> str:
    return cast("str", row[column])


def _integer(row: sqlite3.Row, column: str) -> int:
    return cast("int", row[column])
