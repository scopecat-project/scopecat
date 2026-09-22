"""SQLite receipt ledger for globally idempotent config operations."""

from __future__ import annotations

import sqlite3
from typing import Literal, cast

from pydantic import ValidationError
from pydantic_core import PydanticSerializationError
from scopecat.daemon.wire import (
    ConfigActivationReceipt,
    ConfigContextPublishReceipt,
    ConfigPublishReceipt,
)
from scopecat.kernel.errors import DataIntegrityError, StorageError
from scopecat.kernel.problems import ProblemPhase, StorageLocation, problem

from scopecat_server.storage.sqlite.connection import SQLiteDatabase

type ConfigOperationKind = Literal[
    "activate_entry",
    "publish_revision",
    "publish_context",
]
type ConfigOperationReceipt = (
    ConfigActivationReceipt | ConfigPublishReceipt | ConfigContextPublishReceipt
)

_CONFIG_OPERATIONS_REF = "config-registry/operations"


class SQLiteConfigOperationStore:
    """Exact receipts keyed by one global config-side-effect operation id."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self.sqlite = database
        self.database = database.path

    def find(self, operation_id: str) -> ConfigOperationReceipt | None:
        with self.sqlite.read_connection() as connection:
            return self.find_in_transaction(connection, operation_id)

    def find_in_transaction(
        self,
        connection: sqlite3.Connection,
        operation_id: str,
    ) -> ConfigOperationReceipt | None:
        ref = _operation_ref(operation_id)
        try:
            row = _one(
                connection.execute(
                    """
                    SELECT kind, receipt_json
                    FROM config_operations
                    WHERE operation_id = ?
                    """,
                    (operation_id,),
                )
            )
        except sqlite3.Error as error:
            raise _storage_failure(ref) from error
        if row is None:
            return None
        kind = cast("str", row["kind"])
        receipt_json = cast("str", row["receipt_json"])
        try:
            if kind == "activate_entry":
                receipt: ConfigOperationReceipt = (
                    ConfigActivationReceipt.model_validate_json(receipt_json)
                )
            elif kind == "publish_revision":
                receipt = ConfigPublishReceipt.model_validate_json(receipt_json)
            elif kind == "publish_context":
                receipt = ConfigContextPublishReceipt.model_validate_json(receipt_json)
            else:
                raise ValueError("config operation kind is not supported")
        except (ValidationError, ValueError) as error:
            raise _integrity_failure(ref) from error
        if receipt.operation.operation_id != operation_id:
            raise _integrity_failure(ref)
        return receipt

    def commit_in_transaction(
        self,
        connection: sqlite3.Connection,
        receipt: ConfigOperationReceipt,
    ) -> None:
        kind = config_operation_kind(receipt)
        operation = receipt.operation
        ref = _operation_ref(operation.operation_id)
        try:
            receipt_json = receipt.model_dump_json()
            connection.execute(
                """
                INSERT INTO config_operations(
                    operation_id,
                    kind,
                    intent_hash,
                    expected_generation,
                    result_entry_id,
                    result_activation_generation,
                    receipt_json,
                    recorded_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    operation.operation_id,
                    kind,
                    operation.intent_hash,
                    None
                    if isinstance(
                        receipt,
                        ConfigContextPublishReceipt,
                    )
                    else receipt.operation.expected_generation,
                    operation.entry_id,
                    None
                    if isinstance(
                        receipt,
                        ConfigContextPublishReceipt,
                    )
                    else receipt.operation.activation_generation,
                    receipt_json,
                    operation.recorded_at.isoformat(),
                ),
            )
        except (PydanticSerializationError, TypeError, ValueError) as error:
            raise _integrity_failure(ref) from error
        except sqlite3.Error as error:
            raise _storage_failure(ref) from error


def config_operation_kind(receipt: ConfigOperationReceipt) -> ConfigOperationKind:
    if isinstance(receipt, ConfigContextPublishReceipt):
        return "publish_context"
    if isinstance(receipt, ConfigActivationReceipt):
        return "activate_entry"
    return "publish_revision"


def _operation_ref(operation_id: str) -> str:
    return f"{_CONFIG_OPERATIONS_REF}/{operation_id}.json"


def _integrity_failure(ref: str) -> DataIntegrityError:
    return DataIntegrityError(
        [
            problem(
                "config_operation.receipt_invalid",
                "config operation receipt does not match its durable schema",
                phase=ProblemPhase.CONFIGURATION,
                location=StorageLocation(ref=ref),
            )
        ]
    )


def _storage_failure(ref: str) -> StorageError:
    return StorageError(
        [
            problem(
                "config_operation.storage_failed",
                "storage could not complete the config operation",
                phase=ProblemPhase.CONFIGURATION,
                location=StorageLocation(ref=ref),
            )
        ]
    )


def _one(cursor: sqlite3.Cursor) -> sqlite3.Row | None:
    return cast("sqlite3.Row | None", cursor.fetchone())


__all__ = [
    "ConfigOperationKind",
    "ConfigOperationReceipt",
    "SQLiteConfigOperationStore",
    "config_operation_kind",
]
