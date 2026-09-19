"""SQLite persistence for stable samples and immutable descriptive revisions."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import cast

from pydantic import JsonValue, ValidationError
from scopecat.control.models import DurableEventInput
from scopecat.daemon.views import (
    SamplePage,
    SampleRevisionPage,
    SampleSummary,
    SampleView,
)
from scopecat.daemon.wire import (
    SampleCreateCommand,
    SampleMutationReceipt,
    SampleReviseCommand,
)
from scopecat.kernel.run_outcome import utc_now
from scopecat.records.run import RunSnapshot
from scopecat.records.sample import (
    SampleBinding,
    SampleRecord,
    SampleRevision,
    SampleSelector,
    sample_revision_content_hash,
)

from scopecat_server.errors import BackendConflict, BackendNotFound
from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.control_plane import SQLiteControlPlane
from scopecat_server.storage.sqlite.experimental_batches import require_batch


class SQLiteSampleStore:
    """Own the project sample index and run-to-sample provenance bindings."""

    def __init__(
        self,
        database: SQLiteDatabase,
        *,
        control: SQLiteControlPlane,
    ) -> None:
        self.sqlite = database
        self._control = control

    def list_samples(
        self,
        *,
        limit: int,
        before: int | None = None,
    ) -> SamplePage:
        clauses = "" if before is None else "WHERE s.sequence < ?"
        parameters: list[int] = [] if before is None else [before]
        parameters.append(limit + 1)
        try:
            with self.sqlite.read_connection() as connection:
                rows = cast(
                    "list[sqlite3.Row]",
                    connection.execute(
                        f"""
                        SELECT
                            s.sequence,
                            s.record_json,
                            sr.revision_json,
                            COUNT(rsb.run_id) AS run_count,
                            MAX(r.created_at) AS last_run_at
                        FROM samples AS s
                        JOIN sample_revisions AS sr
                          ON sr.sample_id = s.sample_id
                         AND sr.revision = s.active_revision
                        LEFT JOIN run_sample_bindings AS rsb
                          ON rsb.sample_id = s.sample_id
                        LEFT JOIN runs AS r ON r.run_id = rsb.run_id
                        {clauses}
                        GROUP BY s.sequence, s.record_json, sr.revision_json
                        ORDER BY s.sequence DESC
                        LIMIT ?
                        """,  # noqa: S608 - clause is a fixed internal fragment
                        parameters,
                    ).fetchall(),
                )
        except sqlite3.Error as error:
            raise BackendConflict("could not read the sample registry") from error
        selected = rows[:limit]
        try:
            return SamplePage(
                items=tuple(_summary(row) for row in selected),
                next_cursor=(
                    cast("int", selected[-1]["sequence"]) if len(rows) > limit else None
                ),
            )
        except ValidationError as error:
            raise BackendConflict("sample registry contains invalid records") from error

    def get_sample(self, sample_id: str) -> SampleView:
        try:
            with self.sqlite.read_connection() as connection:
                row = cast(
                    "sqlite3.Row | None",
                    connection.execute(
                        """
                        SELECT
                            s.record_json,
                            sr.revision_json,
                            COUNT(rsb.run_id) AS run_count,
                            MAX(r.created_at) AS last_run_at
                        FROM samples AS s
                        JOIN sample_revisions AS sr
                          ON sr.sample_id = s.sample_id
                         AND sr.revision = s.active_revision
                        LEFT JOIN run_sample_bindings AS rsb
                          ON rsb.sample_id = s.sample_id
                        LEFT JOIN runs AS r ON r.run_id = rsb.run_id
                        WHERE s.sample_id = ?
                        GROUP BY s.record_json, sr.revision_json
                        """,
                        (sample_id,),
                    ).fetchone(),
                )
        except sqlite3.Error as error:
            raise BackendConflict(f"could not read sample {sample_id!r}") from error
        if row is None:
            raise BackendNotFound(f"unknown sample {sample_id!r}")
        try:
            summary = _summary(row)
            return SampleView(
                record=summary.record,
                revision=summary.revision,
                run_count=summary.run_count,
                last_run_at=summary.last_run_at,
            )
        except ValidationError as error:
            raise BackendConflict(
                f"sample {sample_id!r} contains invalid records"
            ) from error

    def list_revisions(
        self,
        sample_id: str,
        *,
        limit: int,
        before: int | None = None,
    ) -> SampleRevisionPage:
        clauses = "" if before is None else "AND revision < ?"
        parameters: list[str | int] = [sample_id]
        if before is not None:
            parameters.append(before)
        parameters.append(limit + 1)
        try:
            with self.sqlite.read_connection() as connection:
                if _sample_row(connection, sample_id) is None:
                    raise BackendNotFound(f"unknown sample {sample_id!r}")
                rows = cast(
                    "list[sqlite3.Row]",
                    connection.execute(
                        f"""
                        SELECT revision, revision_json
                        FROM sample_revisions
                        WHERE sample_id = ? {clauses}
                        ORDER BY revision DESC
                        LIMIT ?
                        """,  # noqa: S608 - clause is a fixed internal fragment
                        parameters,
                    ).fetchall(),
                )
        except sqlite3.Error as error:
            raise BackendConflict(
                f"could not read revisions for sample {sample_id!r}"
            ) from error
        selected = rows[:limit]
        try:
            return SampleRevisionPage(
                sample_id=sample_id,
                items=tuple(
                    SampleRevision.model_validate_json(
                        cast("str", row["revision_json"])
                    )
                    for row in selected
                ),
                next_cursor=(
                    cast("int", selected[-1]["revision"]) if len(rows) > limit else None
                ),
            )
        except ValidationError as error:
            raise BackendConflict(
                f"sample {sample_id!r} contains invalid revisions"
            ) from error

    def get_revision(self, sample_id: str, revision: int) -> SampleRevision:
        try:
            with self.sqlite.read_connection() as connection:
                row = cast(
                    "sqlite3.Row | None",
                    connection.execute(
                        """
                        SELECT revision_json
                        FROM sample_revisions
                        WHERE sample_id = ? AND revision = ?
                        """,
                        (sample_id, revision),
                    ).fetchone(),
                )
        except sqlite3.Error as error:
            raise BackendConflict(
                f"could not read sample {sample_id!r} revision {revision}"
            ) from error
        if row is None:
            raise BackendNotFound(f"sample {sample_id!r} has no revision {revision}")
        try:
            return SampleRevision.model_validate_json(cast("str", row["revision_json"]))
        except ValidationError as error:
            raise BackendConflict(
                f"sample {sample_id!r} revision {revision} is invalid"
            ) from error

    def create_sample(self, command: SampleCreateCommand) -> SampleMutationReceipt:
        with self.sqlite.write_transaction() as connection:
            replay = self._replay(connection, command.operation_id, command.intent_hash)
            if replay is not None:
                return replay
            if _sample_row(connection, command.sample_id) is not None:
                raise BackendConflict(f"sample {command.sample_id!r} already exists")
            self._validate_relations(
                connection,
                sample_id=command.sample_id,
                related_ids=tuple(item.sample_id for item in command.content.relations),
            )
            recorded_at = utc_now()
            record = SampleRecord(
                id=command.sample_id,
                kind=command.kind,
                created_at=recorded_at,
                active_revision=1,
            )
            revision = SampleRevision(
                sample_id=command.sample_id,
                revision=1,
                content_hash=sample_revision_content_hash(
                    sample_id=command.sample_id,
                    content=command.content,
                ),
                recorded_at=recorded_at,
                actor=command.actor,
                note=command.note,
                content=command.content,
            )
            connection.execute(
                """
                INSERT INTO samples(
                    sample_id, kind, created_at, active_revision, record_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.kind,
                    record.created_at.isoformat(),
                    record.active_revision,
                    record.model_dump_json(),
                ),
            )
            self._insert_revision(connection, revision)
            receipt = SampleMutationReceipt(
                operation_id=command.operation_id,
                record=record,
                revision=revision,
            )
            self._record_operation(connection, command.intent_hash, receipt)
            self._control.append_event_in_transaction(
                connection,
                DurableEventInput(
                    kind="sample_created",
                    payload=_sample_event_payload(receipt),
                ),
            )
            return receipt

    def revise_sample(
        self,
        sample_id: str,
        command: SampleReviseCommand,
    ) -> SampleMutationReceipt:
        with self.sqlite.write_transaction() as connection:
            replay = self._replay(connection, command.operation_id, command.intent_hash)
            if replay is not None:
                if replay.record.id != sample_id:
                    raise BackendConflict(
                        "sample operation belongs to a different sample"
                    )
                return replay
            row = _sample_row(connection, sample_id)
            if row is None:
                raise BackendNotFound(f"unknown sample {sample_id!r}")
            record = SampleRecord.model_validate_json(cast("str", row["record_json"]))
            if record.active_revision != command.expected_revision:
                raise BackendConflict(
                    f"sample {sample_id!r} is at revision "
                    f"{record.active_revision}, not {command.expected_revision}"
                )
            self._validate_relations(
                connection,
                sample_id=sample_id,
                related_ids=tuple(item.sample_id for item in command.content.relations),
            )
            next_revision = record.active_revision + 1
            revision = SampleRevision(
                sample_id=sample_id,
                revision=next_revision,
                content_hash=sample_revision_content_hash(
                    sample_id=sample_id,
                    content=command.content,
                ),
                actor=command.actor,
                note=command.note,
                content=command.content,
            )
            record = record.model_copy(update={"active_revision": next_revision})
            self._insert_revision(connection, revision)
            connection.execute(
                """
                UPDATE samples
                SET active_revision = ?, record_json = ?
                WHERE sample_id = ?
                """,
                (next_revision, record.model_dump_json(), sample_id),
            )
            receipt = SampleMutationReceipt(
                operation_id=command.operation_id,
                record=record,
                revision=revision,
            )
            self._record_operation(connection, command.intent_hash, receipt)
            self._control.append_event_in_transaction(
                connection,
                DurableEventInput(
                    kind="sample_revision_activated",
                    payload=_sample_event_payload(receipt),
                ),
            )
            return receipt

    def resolve_bindings(
        self,
        selectors: tuple[SampleSelector, ...],
    ) -> tuple[SampleBinding, ...]:
        try:
            with self.sqlite.read_connection() as connection:
                return self.resolve_bindings_in_transaction(connection, selectors)
        except sqlite3.Error as error:
            raise BackendConflict("could not resolve run samples") from error

    def resolve_bindings_in_transaction(
        self,
        connection: sqlite3.Connection,
        selectors: tuple[SampleSelector, ...],
    ) -> tuple[SampleBinding, ...]:
        """Freeze sample heads within the caller's admission transaction."""
        roles = tuple(selector.role for selector in selectors)
        if len(roles) != len(set(roles)):
            raise BackendConflict("run sample roles must be unique")
        sample_ids = tuple(selector.sample_id for selector in selectors)
        if len(sample_ids) != len(set(sample_ids)):
            raise BackendConflict("one sample cannot fill multiple run roles")
        return tuple(
            self._resolve_binding(connection, selector) for selector in selectors
        )

    def bind_run_in_transaction(
        self,
        connection: sqlite3.Connection,
        snapshot: RunSnapshot,
    ) -> None:
        for binding in snapshot.samples:
            require_batch(connection, binding.batch_id)
            if binding.batch_id is not None:
                connection.execute(
                    "INSERT INTO run_sample_batches VALUES (?,?,?)",
                    (snapshot.run_id, binding.role, binding.batch_id),
                )
            row = cast(
                "sqlite3.Row | None",
                connection.execute(
                    """
                    SELECT content_hash
                    FROM sample_revisions
                    WHERE sample_id = ? AND revision = ?
                    """,
                    (binding.sample_id, binding.revision),
                ).fetchone(),
            )
            if row is None or row["content_hash"] != binding.content_hash:
                raise BackendConflict(
                    f"sample binding {binding.sample_id!r} cannot be resolved"
                )
            connection.execute(
                """
                INSERT INTO run_sample_bindings(
                    run_id, role, sample_id, revision, content_hash,
                    context_id, binding_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.run_id,
                    binding.role,
                    binding.sample_id,
                    binding.revision,
                    binding.content_hash,
                    binding.context_id,
                    binding.model_dump_json(),
                ),
            )

    @staticmethod
    def _resolve_binding(
        connection: sqlite3.Connection,
        selector: SampleSelector,
    ) -> SampleBinding:
        require_batch(connection, selector.batch_id)
        row = _sample_row(connection, selector.sample_id)
        if row is None:
            raise BackendNotFound(f"unknown sample {selector.sample_id!r}")
        record = SampleRecord.model_validate_json(cast("str", row["record_json"]))
        selected_revision = selector.revision or record.active_revision
        revision_row = cast(
            "sqlite3.Row | None",
            connection.execute(
                """
                SELECT revision_json
                FROM sample_revisions
                WHERE sample_id = ? AND revision = ?
                """,
                (selector.sample_id, selected_revision),
            ).fetchone(),
        )
        if revision_row is None:
            raise BackendNotFound(
                f"sample {selector.sample_id!r} has no revision {selected_revision}"
            )
        revision = SampleRevision.model_validate_json(
            cast("str", revision_row["revision_json"])
        )
        return SampleBinding(
            role=selector.role,
            sample_id=selector.sample_id,
            revision=selected_revision,
            content_hash=revision.content_hash,
            kind=record.kind,
            display_name=revision.content.display_name,
            context_id=selector.context_id,
            batch_id=selector.batch_id,
        )

    @staticmethod
    def _insert_revision(
        connection: sqlite3.Connection,
        revision: SampleRevision,
    ) -> None:
        connection.execute(
            """
            INSERT INTO sample_revisions(
                sample_id, revision, content_hash, recorded_at, revision_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                revision.sample_id,
                revision.revision,
                revision.content_hash,
                revision.recorded_at.isoformat(),
                revision.model_dump_json(),
            ),
        )

    @staticmethod
    def _validate_relations(
        connection: sqlite3.Connection,
        *,
        sample_id: str,
        related_ids: tuple[str, ...],
    ) -> None:
        if sample_id in related_ids:
            raise BackendConflict("a sample cannot relate to itself")
        for related_id in related_ids:
            if _sample_row(connection, related_id) is None:
                raise BackendNotFound(f"unknown related sample {related_id!r}")

    @staticmethod
    def _replay(
        connection: sqlite3.Connection,
        operation_id: str,
        intent_hash: str,
    ) -> SampleMutationReceipt | None:
        row = cast(
            "sqlite3.Row | None",
            connection.execute(
                """
                SELECT intent_hash, receipt_json
                FROM sample_mutation_operations
                WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone(),
        )
        if row is None:
            return None
        if row["intent_hash"] != intent_hash:
            raise BackendConflict(
                "sample operation id already exists with different content"
            )
        return SampleMutationReceipt.model_validate_json(
            cast("str", row["receipt_json"])
        )

    @staticmethod
    def _record_operation(
        connection: sqlite3.Connection,
        intent_hash: str,
        receipt: SampleMutationReceipt,
    ) -> None:
        connection.execute(
            """
            INSERT INTO sample_mutation_operations(
                operation_id, intent_hash, receipt_json
            ) VALUES (?, ?, ?)
            """,
            (receipt.operation_id, intent_hash, receipt.model_dump_json()),
        )


def _sample_row(
    connection: sqlite3.Connection,
    sample_id: str,
) -> sqlite3.Row | None:
    return cast(
        "sqlite3.Row | None",
        connection.execute(
            "SELECT record_json FROM samples WHERE sample_id = ?",
            (sample_id,),
        ).fetchone(),
    )


def _summary(row: sqlite3.Row) -> SampleSummary:
    encoded_last_run = cast("str | None", row["last_run_at"])
    return SampleSummary(
        record=SampleRecord.model_validate_json(cast("str", row["record_json"])),
        revision=SampleRevision.model_validate_json(cast("str", row["revision_json"])),
        run_count=cast("int", row["run_count"]),
        last_run_at=(
            None
            if encoded_last_run is None
            else datetime.fromisoformat(encoded_last_run)
        ),
    )


def _sample_event_payload(receipt: SampleMutationReceipt) -> dict[str, JsonValue]:
    return {
        "operation_id": receipt.operation_id,
        "sample_id": receipt.record.id,
        "revision": receipt.revision.revision,
        "content_hash": receipt.revision.content_hash,
        "status": receipt.revision.content.status,
    }


__all__ = ["SQLiteSampleStore"]
