"""Inspect inert originals and atomically derive normal parameter/setup objects."""

from __future__ import annotations

import io
import sqlite3
from typing import cast
from zipfile import ZIP_DEFLATED, ZipFile

from scopecat.config.parameter_resolution import validate_parameter_snapshot
from scopecat.daemon.wire import (
    ParameterBranchCommitCommand,
    ParameterSaveCommand,
    SetupSaveCommand,
)
from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.project_sources import verified_source_files
from scopecat.records.author_revision import AuthorRevisionRef
from scopecat.records.configuration_exchange import (
    ConfigurationDerivation,
    ConfigurationDerive,
    ConfigurationExchange,
    ConfigurationExport,
    ConfigurationImportSummary,
    ConfigurationInspection,
)
from scopecat.records.content import Sha256ContentHash
from scopecat.records.parameter import ParameterSnapshot
from scopecat.records.setup import SetupDefinition

from scopecat_server.errors import BackendConflict, BackendNotFound
from scopecat_server.storage.sqlite.devices import DeviceRepository
from scopecat_server.storage.sqlite.parameter_branches import ParameterBranchRepository
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore

from .author_workspaces import AuthorWorkspaceServices
from .config import ConfigService
from .setup import SetupService

MAX_EXCHANGE_BYTES = 16 * 1024 * 1024


class ConfigurationExchangeService:
    def __init__(
        self,
        store: SQLiteProjectStore,
        config: ConfigService,
        setup: SetupService,
        authors: AuthorWorkspaceServices,
    ) -> None:
        self._store = store
        self._config = config
        self._setup = setup
        self._authors = authors

    def export(self, command: ConfigurationExport) -> ConfigurationExchange:
        revision = self._config.parameter_revision(command.parameter_revision)
        if (command.workspace is None) != (command.source_revision is None):
            raise ValueError(
                "Select an exact retained source revision and its workspace together"
            )
        source = (
            self._authors.repository(command.workspace).get(
                AuthorRevisionRef(content_hash=command.source_revision)
            )
            if command.workspace is not None and command.source_revision is not None
            else None
        )
        document = ConfigurationExchange(
            label=command.label,
            origin_store=self._store.identity(),
            parameter_origin=revision.ref,
            catalog=revision.catalog,
            parameters=revision.parameters
            if command.include_values
            else ParameterSnapshot(id=revision.parameters.id, values=[]),
            values_included=command.include_values,
            setup=self._setup.definition(command.setup_definition)
            if command.setup_definition
            else None,
            source=source,
        )
        self.inspect(document)
        return document

    def inspect(self, document: ConfigurationExchange) -> ConfigurationInspection:
        if len(document.model_dump_json().encode()) > MAX_EXCHANGE_BYTES:
            raise ValueError("Configuration exchange exceeds the 16 MiB limit")
        if not document.values_included and document.parameters.values:
            raise ValueError("Definitions-only selection contains parameter values")
        problems = validate_parameter_snapshot(
            document.catalog, document.parameters, allow_missing=True
        )
        if problems:
            raise ValueError(f"Parameter schema/value mismatch: {problems}")
        source_files = (
            tuple(name for name, _ in verified_source_files(document.source))
            if document.source
            else ()
        )
        notices = ["Imported values are initial inputs, not verified calibration."]
        if document.setup:
            notices.append(
                "Map every device role explicitly; sender device IDs are not "
                "local bindings."
            )
        if document.source:
            notices.append(
                "Source has not been accepted, registered or executed. Execution "
                "environment and dependencies have not been checked."
            )
            notices.append(
                "Method descriptions and dynamic controls require later trusted "
                "loading; inspection does not infer arbitrary Python behavior."
            )
        else:
            notices.append(
                "No source is attached. Select existing local author code in "
                "Application settings before preparing an experiment."
            )
        return ConfigurationInspection(
            content_hash=document.content_hash,
            document=document,
            source_files=source_files,
            requirements=document.source.manifest.import_requirements
            if document.source
            else (),
            notices=tuple(notices),
        )

    @staticmethod
    def _retain(
        connection: sqlite3.Connection, document: ConfigurationExchange
    ) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO configuration_imports VALUES (?, ?)",
            (document.content_hash, document.model_dump_json()),
        )

    def retain(self, document: ConfigurationExchange) -> ConfigurationInspection:
        inspection = self.inspect(document)
        with self._store.sqlite.write_transaction() as connection:
            self._retain(connection, document)
        return inspection

    def read(self, content_hash: Sha256ContentHash) -> ConfigurationInspection:
        with self._store.sqlite.read_connection() as connection:
            row = cast(
                "sqlite3.Row | None",
                connection.execute(
                    "SELECT document_json FROM configuration_imports "
                    "WHERE content_hash=?",
                    (content_hash,),
                ).fetchone(),
            )
        if row is None:
            raise BackendNotFound("Configuration import not found")
        document = ConfigurationExchange.model_validate_json(cast("str", row[0]))
        if document.content_hash != content_hash:
            raise ValueError(
                "Retained configuration identity does not match its content"
            )
        return self.inspect(document)

    def list(self) -> tuple[ConfigurationImportSummary, ...]:
        with self._store.sqlite.read_connection() as connection:
            rows = cast(
                "list[sqlite3.Row]",
                connection.execute(
                    "SELECT content_hash, document_json FROM configuration_imports "
                    "ORDER BY rowid DESC LIMIT 100"
                ).fetchall(),
            )
            result: list[ConfigurationImportSummary] = []
            for row in rows:
                document = ConfigurationExchange.model_validate_json(
                    cast("str", row[1])
                )
                receipts = cast(
                    "list[sqlite3.Row]",
                    connection.execute(
                        "SELECT receipt_json FROM configuration_derivations WHERE "
                        "content_hash=? ORDER BY rowid",
                        (row[0],),
                    ).fetchall(),
                )
                result.append(
                    ConfigurationImportSummary(
                        content_hash=document.content_hash,
                        label=document.label,
                        origin_store=document.origin_store,
                        derivations=tuple(
                            ConfigurationDerivation.model_validate_json(
                                cast("str", r[0])
                            )
                            for r in receipts
                        ),
                    )
                )
        return tuple(result)

    def derive(self, command: ConfigurationDerive) -> ConfigurationDerivation:
        document = command.document
        self.inspect(document)
        intent = sha256_json_hash(command.model_dump(mode="json"))
        note = (
            f"Derived from configuration {document.content_hash}; "
            f"source store {document.origin_store}; "
            f"parameter revision {document.parameter_origin.revision_id}. "
            "Initial inputs, not transferred calibration qualification."
        )
        if command.include_setup and document.setup is None:
            raise ValueError("No setup was included")
        if not command.include_setup and command.bindings:
            raise ValueError("Device mappings require explicitly selecting the setup")
        with self._store.sqlite.write_transaction() as connection:
            previous = cast(
                "sqlite3.Row | None",
                connection.execute(
                    "SELECT intent_hash, receipt_json FROM configuration_derivations "
                    "WHERE operation_id=?",
                    (command.operation_id,),
                ).fetchone(),
            )
            if previous is not None:
                if previous[0] != intent:
                    raise BackendConflict(
                        "Import request changed; review and use a new operation"
                    )
                return ConfigurationDerivation.model_validate_json(
                    cast("str", previous[1])
                )
            try:
                ParameterBranchRepository(connection).get(command.name)
            except KeyError:
                pass
            else:
                raise BackendConflict(
                    "A parameter branch already uses this name; choose a new name"
                )
            self._retain(connection, document)
            branch = self._config.commit_parameter_branch_in_transaction(
                connection,
                ParameterBranchCommitCommand(
                    name=command.name,
                    expected_generation=0,
                    actor=command.actor,
                    note=note,
                    source=ParameterSaveCommand(
                        revision_id=command.name,
                        catalog=document.catalog,
                        parameters=document.parameters,
                        actor=command.actor,
                        note=note,
                    ),
                ),
            )
            setup = None
            if command.include_setup and document.setup is not None:
                definition = document.setup.definition
                if set(command.bindings) != {
                    item.id for item in definition.instruments
                }:
                    raise ValueError("Select a local device for every imported role")
                devices = DeviceRepository(connection)
                for reference in command.bindings.values():
                    try:
                        devices.require_current((reference,))
                    except KeyError as error:
                        raise BackendNotFound(
                            "Selected local device no longer exists"
                        ) from error
                mapped = SetupDefinition.model_validate(
                    {
                        **definition.model_dump(mode="python"),
                        "instruments": tuple(
                            item.model_copy(
                                update={
                                    "device_id": command.bindings[item.id].device_id
                                }
                            )
                            for item in definition.instruments
                        ),
                    }
                )
                setup = self._setup.save_in_transaction(
                    connection,
                    SetupSaveCommand(
                        revision_id=f"{command.name} setup",
                        setup=mapped,
                        actor=command.actor,
                        note=note,
                    ),
                )
            receipt = ConfigurationDerivation(
                content_hash=document.content_hash,
                branch=branch,
                setup=setup,
                source_pending=document.source is not None,
                setup_pending=document.setup is not None and setup is None,
            )
            connection.execute(
                "INSERT INTO configuration_derivations VALUES (?, ?, ?, ?)",
                (
                    document.content_hash,
                    command.operation_id,
                    intent,
                    receipt.model_dump_json(),
                ),
            )
            return receipt

    def source_archive(
        self, content_hash: Sha256ContentHash, *, accepted: bool
    ) -> bytes:
        if not accepted:
            raise ValueError("Explicitly accept the source before downloading it")
        source = self.read(content_hash).document.source
        if source is None:
            raise ValueError("No source is attached")
        output = io.BytesIO()
        with ZipFile(output, "w", ZIP_DEFLATED) as archive:
            for name, content in verified_source_files(source):
                archive.writestr(name, content)
        return output.getvalue()
