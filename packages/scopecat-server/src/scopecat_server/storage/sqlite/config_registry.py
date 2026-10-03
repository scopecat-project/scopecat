"""SQLite persistence for the daemon-owned configuration registry."""

from __future__ import annotations

import sqlite3
from contextlib import AbstractContextManager
from types import TracebackType
from typing import Self, cast

from pydantic import BaseModel, ValidationError
from pydantic_core import PydanticSerializationError
from scopecat.config.registry.records import (
    ConfigRegistryEntry,
    ConfigRegistryEntryPage,
)
from scopecat.kernel.errors import (
    DataIntegrityError,
    StorageError,
)
from scopecat.kernel.problems import (
    ProblemPhase,
    StorageLocation,
    problem,
)
from scopecat.records.config import ConfigProfileSnapshot, SystemSpec
from scopecat.records.parameter_revision import ParameterRevisionContent
from scopecat.records.setup import ExecutableSetupSnapshot
from scopecat.runs.repository import RunRepository

from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.setups import SQLiteSetupRepository

CONFIG_REGISTRY_ROOT = "config-registry"


class SQLiteConfigRegistryRepository:
    """Registry view bound to one project transaction."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

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
                    SELECT entries.parameters_json, entries.setup_content_hash,
                           setups.setup_json
                    FROM config_registry_entries AS entries
                    LEFT JOIN configuration_setup_contents AS setups
                      ON setups.content_hash = entries.setup_content_hash
                    WHERE entries.config_ref = ?
                    """,
                    (ref,),
                )
            )
        except sqlite3.Error as error:
            raise _storage_failure(ref) from error
        if row is None:
            raise _missing_record(ref)
        parameters = _parse_model(
            _text(row, "parameters_json"),
            ParameterRevisionContent,
            ref=ref,
            code="config_registry.config_invalid",
        )
        setup = _parse_model(
            _text(row, "setup_json"),
            ExecutableSetupSnapshot,
            ref=ref,
            code="config_registry.setup_invalid",
        )
        if setup.content_hash != _text(row, "setup_content_hash"):
            raise _integrity_failure(
                ref,
                code="config_registry.setup_hash_mismatch",
                message="retained setup content does not match its reference",
            )
        return ConfigProfileSnapshot(
            id=parameters.id,
            system=SystemSpec.model_validate(
                {
                    **setup.model_dump(),
                    "id": parameters.system_id,
                    "parameter_catalog": parameters.catalog,
                }
            ),
            parameter_snapshot=parameters.parameters,
        )

    def commit_revision(
        self,
        *,
        entry: ConfigRegistryEntry,
        config: ConfigProfileSnapshot,
    ) -> None:
        setup = ExecutableSetupSnapshot.from_config(config)
        parameters = ParameterRevisionContent(
            id=config.id,
            system_id=config.system.id,
            catalog=config.parameter_catalog,
            parameters=config.parameter_snapshot,
        )
        try:
            self._connection.execute(
                """
                INSERT INTO configuration_setup_contents(content_hash, setup_json)
                VALUES (?, ?) ON CONFLICT(content_hash) DO NOTHING
                """,
                (setup.content_hash, _encode_model(setup, ref=entry.config_ref)),
            )
            self._connection.execute(
                """
                INSERT INTO config_registry_entries(
                    entry_id,
                    config_ref,
                    entry_json,
                    parameters_json,
                    setup_content_hash,
                    recorded_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(entry_id) DO NOTHING
                """,
                (
                    entry.id,
                    entry.config_ref,
                    _encode_model(entry, ref=self.entry_ref(entry.id)),
                    _encode_model(parameters, ref=entry.config_ref),
                    setup.content_hash,
                    entry.recorded_at.isoformat(),
                ),
            )
        except sqlite3.Error as error:
            raise _storage_failure(self.entry_ref(entry.id)) from error


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
        self._setups: SQLiteSetupRepository | None = None
        self._transaction: AbstractContextManager[sqlite3.Connection] | None = None

    @property
    def registry(self) -> SQLiteConfigRegistryRepository:
        if self._registry is None:
            msg = "config registry unit of work has not been entered"
            raise RuntimeError(msg)
        return self._registry

    @property
    def setups(self) -> SQLiteSetupRepository:
        if self._setups is None:
            raise RuntimeError("config registry unit of work has not been entered")
        return self._setups

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
        self._setups = SQLiteSetupRepository(connection)
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
        self._setups = None
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
