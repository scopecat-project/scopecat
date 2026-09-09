"""SQLite persistence for the daemon-owned configuration registry."""

from __future__ import annotations

import sqlite3
from contextlib import AbstractContextManager
from types import TracebackType
from typing import Self, cast

from pydantic import BaseModel, ValidationError
from pydantic_core import PydanticSerializationError
from scopecat.config.registry.records import (
    ConfigRegistryActivationPage,
    ConfigRegistryActivationRecord,
    ConfigRegistryEntry,
    ConfigRegistryEntryPage,
)
from scopecat.kernel.errors import (
    Conflict,
    DataIntegrityError,
    StorageError,
)
from scopecat.kernel.problems import (
    ModelLocation,
    ProblemPhase,
    StorageLocation,
    problem,
)
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.runs.repository import RunRepository

from scopecat_server.storage.sqlite.connection import SQLiteDatabase

CONFIG_REGISTRY_ROOT = "config-registry"
CONFIG_REGISTRY_ACTIVATIONS_REF = f"{CONFIG_REGISTRY_ROOT}/activations"


class SQLiteConfigRegistryRepository:
    """Registry view bound to one project transaction."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    @property
    def active_ref(self) -> str:
        return CONFIG_REGISTRY_ACTIVATIONS_REF

    def entry_ref(self, entry_id: str) -> str:
        return f"{CONFIG_REGISTRY_ROOT}/entries/{entry_id}.json"

    def config_ref(self, entry_id: str) -> str:
        return f"{CONFIG_REGISTRY_ROOT}/configs/{entry_id}.config-profile-snapshot.json"

    def entry_exists(self, entry_id: str) -> bool:
        try:
            row = _one(
                self._connection.execute(
                    """
                    SELECT 1 AS present
                    FROM config_registry_entries
                    WHERE entry_id = ?
                    """,
                    (entry_id,),
                )
            )
        except sqlite3.Error as error:
            raise _storage_failure(self.entry_ref(entry_id)) from error
        return row is not None

    def list_entries(self) -> tuple[ConfigRegistryEntry, ...]:
        try:
            rows = _all(
                self._connection.execute(
                    """
                    SELECT entry_id, entry_json
                    FROM config_registry_entries
                    ORDER BY recorded_at, entry_id
                    """
                )
            )
        except sqlite3.Error as error:
            raise _storage_failure(CONFIG_REGISTRY_ROOT) from error
        return tuple(
            _parse_model(
                _text(row, "entry_json"),
                ConfigRegistryEntry,
                ref=self.entry_ref(_text(row, "entry_id")),
                code="config_registry.record_invalid",
            )
            for row in rows
        )

    def list_entry_page(
        self,
        *,
        limit: int,
        before: int | None,
    ) -> ConfigRegistryEntryPage:
        try:
            if before is None:
                rows = _all(
                    self._connection.execute(
                        """
                        SELECT rowid AS sequence, entry_id, entry_json
                        FROM config_registry_entries
                        ORDER BY rowid DESC
                        LIMIT ?
                        """,
                        (limit + 1,),
                    )
                )
            else:
                rows = _all(
                    self._connection.execute(
                        """
                        SELECT rowid AS sequence, entry_id, entry_json
                        FROM config_registry_entries
                        WHERE rowid < ?
                        ORDER BY rowid DESC
                        LIMIT ?
                        """,
                        (before, limit + 1),
                    )
                )
        except sqlite3.Error as error:
            raise _storage_failure(CONFIG_REGISTRY_ROOT) from error
        selected = rows[:limit]
        return ConfigRegistryEntryPage(
            items=tuple(
                _parse_model(
                    _text(row, "entry_json"),
                    ConfigRegistryEntry,
                    ref=self.entry_ref(_text(row, "entry_id")),
                    code="config_registry.record_invalid",
                )
                for row in selected
            ),
            next_cursor=(
                _integer(selected[-1], "sequence") if len(rows) > limit else None
            ),
        )

    def read_entry(self, entry_id: str) -> ConfigRegistryEntry:
        ref = self.entry_ref(entry_id)
        try:
            row = _one(
                self._connection.execute(
                    """
                    SELECT entry_json
                    FROM config_registry_entries
                    WHERE entry_id = ?
                    """,
                    (entry_id,),
                )
            )
        except sqlite3.Error as error:
            raise _storage_failure(ref) from error
        if row is None:
            raise _missing_record(ref)
        return _parse_model(
            _text(row, "entry_json"),
            ConfigRegistryEntry,
            ref=ref,
            code="config_registry.record_invalid",
        )

    def read_config(self, ref: str) -> ConfigProfileSnapshot:
        try:
            row = _one(
                self._connection.execute(
                    """
                    SELECT config_json
                    FROM config_registry_entries
                    WHERE config_ref = ?
                    """,
                    (ref,),
                )
            )
        except sqlite3.Error as error:
            raise _storage_failure(ref) from error
        if row is None:
            raise _missing_record(ref)
        return _parse_model(
            _text(row, "config_json"),
            ConfigProfileSnapshot,
            ref=ref,
            code="config_registry.config_invalid",
        )

    def current_generation(self) -> int:
        try:
            return _current_generation(self._connection)
        except sqlite3.Error as error:
            raise _storage_failure(self.active_ref) from error

    def read_latest_activation(self) -> ConfigRegistryActivationRecord | None:
        try:
            row = _one(
                self._connection.execute(
                    """
                    SELECT generation, record_json
                    FROM config_registry_activations
                    ORDER BY generation DESC
                    LIMIT 1
                    """
                )
            )
        except sqlite3.Error as error:
            raise _storage_failure(self.active_ref) from error
        if row is None:
            return None
        return _parse_model(
            _text(row, "record_json"),
            ConfigRegistryActivationRecord,
            ref=f"{self.active_ref}#generation-{_integer(row, 'generation')}",
            code="config_registry.activation_record_invalid",
        )

    def read_latest_entry_activation(
        self, entry_id: str
    ) -> ConfigRegistryActivationRecord | None:
        """Read one prior activation without materializing the history.

        Schema 63 has no entry index, so SQLite may scan activation rows.
        Only the matching record is returned and decoded.
        """

        try:
            row = _one(
                self._connection.execute(
                    """
                    SELECT generation, record_json
                    FROM config_registry_activations
                    WHERE entry_id = ?
                    ORDER BY generation DESC
                    LIMIT 1
                    """,
                    (entry_id,),
                )
            )
        except sqlite3.Error as error:
            raise _storage_failure(self.active_ref) from error
        if row is None:
            return None
        return _parse_model(
            _text(row, "record_json"),
            ConfigRegistryActivationRecord,
            ref=f"{self.active_ref}#generation-{_integer(row, 'generation')}",
            code="config_registry.activation_record_invalid",
        )

    def read_activation(self, generation: int) -> ConfigRegistryActivationRecord:
        ref = f"{self.active_ref}#generation-{generation}"
        try:
            row = _one(
                self._connection.execute(
                    """
                    SELECT generation, record_json
                    FROM config_registry_activations
                    WHERE generation = ?
                    """,
                    (generation,),
                )
            )
        except sqlite3.Error as error:
            raise _storage_failure(ref) from error
        if row is None:
            raise _missing_record(ref)
        return _parse_model(
            _text(row, "record_json"),
            ConfigRegistryActivationRecord,
            ref=ref,
            code="config_registry.activation_record_invalid",
        )

    def list_activation_history(self) -> tuple[ConfigRegistryActivationRecord, ...]:
        try:
            rows = _all(
                self._connection.execute(
                    """
                    SELECT generation, record_json
                    FROM config_registry_activations
                    ORDER BY generation
                    """
                )
            )
        except sqlite3.Error as error:
            raise _storage_failure(self.active_ref) from error
        return tuple(
            _parse_model(
                _text(row, "record_json"),
                ConfigRegistryActivationRecord,
                ref=f"{self.active_ref}#generation-{_integer(row, 'generation')}",
                code="config_registry.activation_record_invalid",
            )
            for row in rows
        )

    def list_activation_page(
        self,
        *,
        limit: int,
        before: int | None,
    ) -> ConfigRegistryActivationPage:
        try:
            if before is None:
                rows = _all(
                    self._connection.execute(
                        """
                        SELECT generation, record_json
                        FROM config_registry_activations
                        ORDER BY generation DESC
                        LIMIT ?
                        """,
                        (limit + 1,),
                    )
                )
            else:
                rows = _all(
                    self._connection.execute(
                        """
                        SELECT generation, record_json
                        FROM config_registry_activations
                        WHERE generation < ?
                        ORDER BY generation DESC
                        LIMIT ?
                        """,
                        (before, limit + 1),
                    )
                )
        except sqlite3.Error as error:
            raise _storage_failure(self.active_ref) from error
        selected = rows[:limit]
        return ConfigRegistryActivationPage(
            items=tuple(
                _parse_model(
                    _text(row, "record_json"),
                    ConfigRegistryActivationRecord,
                    ref=(f"{self.active_ref}#generation-{_integer(row, 'generation')}"),
                    code="config_registry.activation_record_invalid",
                )
                for row in selected
            ),
            next_cursor=(
                _integer(selected[-1], "generation") if len(rows) > limit else None
            ),
        )

    def commit_revision(
        self,
        *,
        entry: ConfigRegistryEntry,
        config: ConfigProfileSnapshot,
    ) -> None:
        try:
            self._connection.execute(
                """
                INSERT INTO config_registry_entries(
                    entry_id,
                    config_ref,
                    entry_json,
                    config_json,
                    recorded_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(entry_id) DO NOTHING
                """,
                (
                    entry.id,
                    entry.config_ref,
                    _encode_model(entry, ref=self.entry_ref(entry.id)),
                    _encode_model(config, ref=entry.config_ref),
                    entry.recorded_at.isoformat(),
                ),
            )
        except sqlite3.Error as error:
            raise _storage_failure(self.entry_ref(entry.id)) from error

    def commit_activation(
        self,
        *,
        expected_generation: int,
        record: ConfigRegistryActivationRecord,
    ) -> None:
        """Append after CAS; the immediate unit of work serializes writers."""

        try:
            current_generation = _current_generation(self._connection)
            if current_generation != expected_generation:
                raise _generation_conflict(
                    expected=expected_generation,
                    actual=current_generation,
                )
            if record.generation != expected_generation + 1:
                raise _generation_conflict(
                    expected=expected_generation,
                    actual=record.generation - 1,
                )
            self._connection.execute(
                """
                INSERT INTO config_registry_activations(
                    generation,
                    entry_id,
                    record_json
                )
                VALUES (?, ?, ?)
                """,
                (
                    record.generation,
                    record.entry_id,
                    _encode_model(record, ref=self.active_ref),
                ),
            )
        except Conflict, DataIntegrityError:
            raise
        except sqlite3.Error as error:
            raise _storage_failure(self.active_ref) from error


class SQLiteConfigRegistryUnitOfWork:
    """One immediate transaction for registry state and injected run reads."""

    def __init__(
        self,
        database: SQLiteDatabase,
        *,
        runs: RunRepository,
        write: bool,
        _borrowed_connection: sqlite3.Connection | None = None,
    ) -> None:
        self.sqlite = database
        self.database = database.path
        self.runs = runs
        self._write = write
        self._borrowed_connection = _borrowed_connection
        self._connection: sqlite3.Connection | None = None
        self._registry: SQLiteConfigRegistryRepository | None = None
        self._transaction: AbstractContextManager[sqlite3.Connection] | None = None

    @property
    def registry(self) -> SQLiteConfigRegistryRepository:
        if self._registry is None:
            msg = "config registry unit of work has not been entered"
            raise RuntimeError(msg)
        return self._registry

    def __enter__(self) -> Self:
        if self._connection is not None:
            msg = "config registry unit of work cannot be entered twice"
            raise RuntimeError(msg)
        connection = self._borrowed_connection
        if connection is None:
            transaction = (
                self.sqlite.write_transaction()
                if self._write
                else self.sqlite.read_transaction()
            )
            try:
                connection = transaction.__enter__()
            except sqlite3.Error as error:
                raise _storage_failure(CONFIG_REGISTRY_ROOT) from error
            self._transaction = transaction
        self._connection = connection
        self._registry = SQLiteConfigRegistryRepository(connection)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        connection = self._connection
        if connection is None:
            msg = "config registry unit of work was not entered"
            raise RuntimeError(msg)
        self._connection = None
        self._registry = None
        if self._borrowed_connection is not None:
            return
        transaction = self._transaction
        self._transaction = None
        assert transaction is not None
        try:
            transaction.__exit__(exc_type, exc_value, traceback)
        except sqlite3.Error as error:
            raise _storage_failure(CONFIG_REGISTRY_ROOT) from error


class SQLiteConfigRegistryStore:
    """Factory for registry transactions sharing a project database."""

    def __init__(
        self,
        database: SQLiteDatabase,
        *,
        runs: RunRepository,
    ) -> None:
        self.sqlite = database
        self.database = database.path
        self.runs = runs

    def read_unit_of_work(self) -> SQLiteConfigRegistryUnitOfWork:
        return SQLiteConfigRegistryUnitOfWork(
            self.sqlite,
            runs=self.runs,
            write=False,
        )

    def write_unit_of_work(self) -> SQLiteConfigRegistryUnitOfWork:
        return SQLiteConfigRegistryUnitOfWork(
            self.sqlite,
            runs=self.runs,
            write=True,
        )

    def borrowed_unit_of_work(
        self,
        connection: sqlite3.Connection,
    ) -> SQLiteConfigRegistryUnitOfWork:
        """Bind registry operations to a caller-owned SQLite transaction."""

        return SQLiteConfigRegistryUnitOfWork(
            self.sqlite,
            runs=self.runs,
            write=True,
            _borrowed_connection=connection,
        )


def _parse_model[TModel: BaseModel](
    content: str,
    model_type: type[TModel],
    *,
    ref: str,
    code: str,
) -> TModel:
    try:
        return model_type.model_validate_json(content)
    except ValidationError as error:
        raise _integrity_failure(
            ref,
            code=code,
            message="config registry record does not match its durable schema",
        ) from error


def _encode_model(model: BaseModel, *, ref: str) -> str:
    try:
        return model.model_dump_json()
    except (PydanticSerializationError, TypeError, ValueError) as error:
        raise _integrity_failure(
            ref,
            code="config_registry.record_not_serializable",
            message="config registry record cannot be represented durably",
        ) from error


def _current_generation(connection: sqlite3.Connection) -> int:
    row = _one(
        connection.execute(
            """
            SELECT COALESCE(MAX(generation), 0) AS generation
            FROM config_registry_activations
            """
        )
    )
    assert row is not None
    return _integer(row, "generation")


def _generation_conflict(*, expected: int, actual: int) -> Conflict:
    return Conflict(
        [
            problem(
                "config_registry.conflict",
                "config registry active state changed",
                phase=ProblemPhase.CONFIGURATION,
                location=ModelLocation(
                    root="config_registry",
                    path=("expected_generation",),
                ),
                related_locations=(
                    StorageLocation(ref=CONFIG_REGISTRY_ACTIVATIONS_REF),
                ),
                details={
                    "expected_generation": expected,
                    "actual_generation": actual,
                },
            )
        ]
    )


def _missing_record(ref: str) -> DataIntegrityError:
    return _integrity_failure(
        ref,
        code="config_registry.record_missing",
        message="config registry is missing a referenced durable record",
    )


def _integrity_failure(
    ref: str,
    *,
    code: str,
    message: str,
) -> DataIntegrityError:
    return DataIntegrityError(
        [
            problem(
                code,
                message,
                phase=ProblemPhase.CONFIGURATION,
                location=StorageLocation(ref=ref),
            )
        ]
    )


def _storage_failure(ref: str) -> StorageError:
    return StorageError(
        [
            problem(
                "config_registry.storage_failed",
                "storage could not complete the config registry operation",
                phase=ProblemPhase.CONFIGURATION,
                location=StorageLocation(ref=ref),
            )
        ]
    )


def _one(cursor: sqlite3.Cursor) -> sqlite3.Row | None:
    return cast("sqlite3.Row | None", cursor.fetchone())


def _all(cursor: sqlite3.Cursor) -> tuple[sqlite3.Row, ...]:
    return cast("tuple[sqlite3.Row, ...]", tuple(cursor.fetchall()))


def _text(row: sqlite3.Row, column: str) -> str:
    return cast("str", row[column])


def _integer(row: sqlite3.Row, column: str) -> int:
    return cast("int", row[column])


__all__ = [
    "SQLiteConfigRegistryRepository",
    "SQLiteConfigRegistryStore",
    "SQLiteConfigRegistryUnitOfWork",
]
