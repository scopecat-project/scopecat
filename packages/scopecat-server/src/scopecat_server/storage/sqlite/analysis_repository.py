"""SQLite index and immutable objects for project-level analyses."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import cast

from pydantic import BaseModel, ValidationError
from pydantic_core import PydanticSerializationError
from scopecat.analysis.repository import (
    AnalysisContentPage,
    AnalysisPublication,
    AnalysisPublicationPage,
    AnalysisPublicationSummary,
)
from scopecat.kernel.errors import Conflict, DataIntegrityError, NotFound, StorageError
from scopecat.kernel.problems import (
    ModelLocation,
    ProblemPhase,
    StorageLocation,
    problem,
)
from scopecat.records.analysis import (
    AnalysisSubject,
    ProjectAnalysisSubject,
    SampleAnalysisSubject,
)
from scopecat.records.content import ContentEntry

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
from scopecat_server.storage.sqlite.resource_objects import (
    put_resource_object,
    resource_objects,
)

_SAFE_RECORD_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_PROJECT_SUBJECT = ProjectAnalysisSubject()


@dataclass(frozen=True, slots=True)
class PreparedAnalysisPublication:
    """Immutable objects prepared before publishing their logical references."""

    publication: AnalysisPublication
    refs: tuple[tuple[str, StoredObject], ...]


class SQLiteAnalysisRepository:
    """Project analysis refs backed by the shared content-addressed store."""

    def __init__(self, database: SQLiteDatabase, objects: str | Path) -> None:
        self.sqlite = database
        self.objects = ImmutableObjectStore(objects)

    def list_summaries(
        self,
        *,
        subject: AnalysisSubject = _PROJECT_SUBJECT,
        limit: int,
        before: int | None,
    ) -> AnalysisPublicationPage:
        """Return one newest-first keyset page without reading content objects."""

        try:
            with self.sqlite.read_connection() as connection:
                return list_publications(
                    connection,
                    subject=subject,
                    limit=limit,
                    before=before,
                )
        except (sqlite3.Error, ValidationError) as error:
            raise _storage_failure("analyses") from error

    def latest_publication(
        self,
        analysis_key: str,
        *,
        subject: AnalysisSubject = _PROJECT_SUBJECT,
    ) -> AnalysisPublicationSummary | None:
        try:
            with self.sqlite.read_connection() as connection:
                return latest_publication(
                    connection,
                    subject=subject,
                    analysis_key=analysis_key,
                )
        except (sqlite3.Error, ValidationError) as error:
            raise _storage_failure(analysis_key) from error

    def read_publication(
        self,
        record_id: str,
        *,
        subject: AnalysisSubject = _PROJECT_SUBJECT,
    ) -> AnalysisPublicationSummary:
        _validate_identity(record_id, "record.json")
        try:
            with self.sqlite.read_connection() as connection:
                publication = read_publication(
                    connection,
                    subject=subject,
                    record_id=record_id,
                )
            if publication is None:
                raise _not_found(record_id)
            return publication
        except (sqlite3.Error, ValidationError) as error:
            raise _storage_failure(record_id) from error

    def list_contents(
        self,
        record_id: str,
        *,
        limit: int,
        before: int | None = None,
    ) -> AnalysisContentPage:
        _validate_identity(record_id, "record.json")
        try:
            with self.sqlite.read_connection() as connection:
                publication_sequence = _publication_sequence(connection, record_id)
                parameters: list[int] = [publication_sequence]
                before_clause = ""
                if before is not None:
                    before_clause = "AND sequence < ?"
                    parameters.append(before)
                parameters.append(limit + 1)
                rows = cast(
                    "list[sqlite3.Row]",
                    connection.execute(
                        f"""
                        SELECT sequence, entry_json
                        FROM project_analysis_contents
                        WHERE publication_sequence = ? {before_clause}
                        ORDER BY sequence DESC
                        LIMIT ?
                        """,  # noqa: S608 - before clause is a fixed fragment
                        parameters,
                    ).fetchall(),
                )
            selected = rows[:limit]
            return AnalysisContentPage(
                items=tuple(
                    ContentEntry.model_validate_json(cast("str", row["entry_json"]))
                    for row in selected
                ),
                next_cursor=(
                    cast("int", selected[-1]["sequence"]) if len(rows) > limit else None
                ),
            )
        except NotFound:
            raise
        except (sqlite3.Error, ValidationError) as error:
            raise _storage_failure(record_id) from error

    def read_content(self, record_id: str, content_id: str) -> ContentEntry:
        _validate_identity(record_id, "record.json")
        try:
            with self.sqlite.read_connection() as connection:
                publication_sequence = _publication_sequence(connection, record_id)
                row = cast(
                    "sqlite3.Row | None",
                    connection.execute(
                        """
                        SELECT entry_json
                        FROM project_analysis_contents
                        WHERE publication_sequence = ? AND content_id = ?
                        """,
                        (publication_sequence, content_id),
                    ).fetchone(),
                )
            if row is None:
                raise _content_not_found(record_id, content_id)
            return ContentEntry.model_validate_json(cast("str", row["entry_json"]))
        except NotFound:
            raise
        except (sqlite3.Error, ValidationError) as error:
            raise _storage_failure(record_id) from error

    def publish(self, publication: AnalysisPublication) -> None:
        prepared = self.prepare_publication(publication)
        try:
            with self.sqlite.write_transaction() as connection:
                self.publish_prepared_in_transaction(connection, prepared)
        except Conflict, NotFound, DataIntegrityError:
            raise
        except sqlite3.Error as error:
            raise _storage_failure(publication.record.id) from error

    def prepare_publication(
        self,
        publication: AnalysisPublication,
    ) -> PreparedAnalysisPublication:
        """Write immutable objects without publishing their logical refs."""

        if not isinstance(
            publication.subject,
            ProjectAnalysisSubject | SampleAnalysisSubject,
        ):
            raise TypeError("project analysis repository requires a project owner")
        record_id = publication.record.id
        prepared: list[tuple[str, StoredObject]] = []
        try:
            for write in publication.models:
                _validate_identity(record_id, write.ref)
                prepared.append(
                    (
                        write.ref,
                        put_resource_object(
                            self.sqlite,
                            self.objects,
                            "analysis",
                            record_id,
                            _encode_model(write.value),
                        ),
                    )
                )
            for write in publication.bytes:
                _validate_identity(record_id, write.ref)
                prepared.append(
                    (
                        write.ref,
                        put_resource_object(
                            self.sqlite,
                            self.objects,
                            "analysis",
                            record_id,
                            write.content,
                        ),
                    )
                )
            return PreparedAnalysisPublication(
                publication=publication,
                refs=tuple(prepared),
            )
        except Conflict, NotFound:
            raise
        except (
            OSError,
            ObjectStoreError,
            PydanticSerializationError,
            TypeError,
            ValueError,
        ) as error:
            raise _storage_failure(record_id) from error

    def publish_prepared_in_transaction(
        self,
        connection: sqlite3.Connection,
        prepared: PreparedAnalysisPublication,
    ) -> bool:
        """Publish prepared refs through the caller's SQLite transaction."""

        publication = prepared.publication
        record_id = publication.record.id
        from .data_cleanup import require_retained_resource

        require_retained_resource(connection, "analysis", record_id)
        try:
            publication_sequence, created = insert_publication(connection, publication)
            if not created:
                return False
            connection.executemany(
                """
                INSERT INTO project_analysis_contents(
                    publication_sequence, role, content_id, kind, produced_by,
                    entry_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    (
                        publication_sequence,
                        entry.role,
                        entry.id,
                        entry.kind,
                        entry.produced_by,
                        entry.model_dump_json(),
                    )
                    for entry in publication.entries
                ),
            )
            connection.executemany(
                """
                INSERT INTO project_analysis_repository_refs(
                    publication_sequence, ref, digest
                )
                VALUES (?, ?, ?)
                """,
                (
                    (publication_sequence, ref, stored.digest)
                    for ref, stored in prepared.refs
                ),
            )
            return True
        except AnalysisIndexConflict as error:
            raise _conflict(record_id) from error
        except Conflict, NotFound:
            raise
        except sqlite3.IntegrityError as error:
            raise _conflict(record_id) from error
        except sqlite3.Error as error:
            raise _storage_failure(record_id) from error

    def read_model[TModel: BaseModel](
        self,
        record_id: str,
        ref: str,
        model_type: type[TModel],
    ) -> TModel:
        try:
            return model_type.model_validate_json(self.read_bytes(record_id, ref))
        except ValidationError as error:
            raise _invalid_ref(record_id, ref) from error

    def read_bytes(self, record_id: str, ref: str) -> bytes:
        _validate_identity(record_id, ref)
        try:
            with self.sqlite.read_connection() as connection:
                publication_sequence = _publication_sequence(connection, record_id)
                row = cast(
                    "sqlite3.Row | None",
                    connection.execute(
                        """
                        SELECT digest FROM project_analysis_repository_refs
                        WHERE publication_sequence = ? AND ref = ?
                        """,
                        (publication_sequence, ref),
                    ).fetchone(),
                )
            if row is None:
                raise _invalid_ref(record_id, ref)
            return resource_objects(
                self.sqlite, self.objects, "analysis", record_id
            ).read(cast("str", row["digest"]))
        except DataIntegrityError:
            raise
        except (ObjectNotFoundError, ObjectCorruptError) as error:
            raise _invalid_ref(record_id, ref) from error
        except (sqlite3.Error, ObjectStoreError) as error:
            raise _storage_failure(record_id) from error


def _encode_model(model: BaseModel) -> bytes:
    return (
        json.dumps(
            model.model_dump(mode="json"),
            allow_nan=True,
            separators=(",", ":"),
        ).encode()
        + b"\n"
    )


def _publication_sequence(connection: sqlite3.Connection, record_id: str) -> int:
    row = cast(
        "sqlite3.Row | None",
        connection.execute(
            """
            SELECT sequence
            FROM analysis_publications
            WHERE subject_kind IN ('project', 'sample') AND record_id = ?
            """,
            (record_id,),
        ).fetchone(),
    )
    if row is None:
        raise _not_found(record_id)
    return cast("int", row["sequence"])


def _validate_identity(record_id: str, ref: str) -> None:
    relative = PurePosixPath(ref)
    if (
        _SAFE_RECORD_ID.fullmatch(record_id) is None
        or not relative.parts
        or relative.is_absolute()
        or ".." in relative.parts
    ):
        raise DataIntegrityError(
            [
                problem(
                    "analysis.identity_invalid",
                    "analysis record id and ref must be safe storage identities",
                    phase=ProblemPhase.PERSISTENCE,
                    location=ModelLocation(root="analysis_ref", path=("ref",)),
                )
            ]
        )


def _not_found(record_id: str) -> NotFound:
    return NotFound(
        [
            problem(
                "analysis.not_found",
                "analysis publication was not found",
                phase=ProblemPhase.PERSISTENCE,
                location=StorageLocation(ref=f"analyses/{record_id}"),
            )
        ]
    )


def _content_not_found(record_id: str, content_id: str) -> NotFound:
    return NotFound(
        [
            problem(
                "analysis.content_not_found",
                "analysis content was not found",
                phase=ProblemPhase.PERSISTENCE,
                location=StorageLocation(
                    ref=f"analyses/{record_id}/contents/{content_id}"
                ),
            )
        ]
    )


def _conflict(record_id: str) -> Conflict:
    return Conflict(
        [
            problem(
                "analysis.publication_conflict",
                "analysis publication identity already contains different content",
                phase=ProblemPhase.PERSISTENCE,
                location=StorageLocation(ref=f"analyses/{record_id}"),
            )
        ]
    )


def _invalid_ref(record_id: str, ref: str) -> DataIntegrityError:
    return DataIntegrityError(
        [
            problem(
                "analysis.ref_invalid",
                "analysis content does not match its durable reference",
                phase=ProblemPhase.PERSISTENCE,
                location=StorageLocation(ref=f"analyses/{record_id}/{ref}"),
            )
        ]
    )


def _storage_failure(ref: str) -> StorageError:
    return StorageError(
        [
            problem(
                "storage.operation_failed",
                "storage could not complete the analysis repository operation",
                phase=ProblemPhase.PERSISTENCE,
                location=StorageLocation(ref=ref),
            )
        ]
    )


__all__ = ["SQLiteAnalysisRepository"]
