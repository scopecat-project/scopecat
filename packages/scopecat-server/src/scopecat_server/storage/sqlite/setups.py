"""Transaction-local executable setup persistence."""

from __future__ import annotations

import sqlite3
from typing import cast

from scopecat.records.setup import (
    SetupDefinitionRevision,
    SetupDeviceResolution,
    SetupRevision,
    resolved_setup_hash,
)

from scopecat_server.storage.sqlite.devices import DeviceRepository


class SQLiteSetupRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def definition(self, definition_id: str) -> SetupDefinitionRevision:
        row = cast(
            "sqlite3.Row | None",
            self._connection.execute(
                "SELECT record_json FROM setup_definitions WHERE definition_id = ?",
                (definition_id,),
            ).fetchone(),
        )
        if row is None:
            raise KeyError(definition_id)
        return SetupDefinitionRevision.model_validate_json(cast("str", row[0]))

    def definitions(self) -> tuple[SetupDefinitionRevision, ...]:
        rows = cast(
            "list[sqlite3.Row]",
            self._connection.execute(
                "SELECT record_json FROM setup_definitions d WHERE NOT EXISTS "
                "(SELECT 1 FROM practice_resources p WHERE p.kind='setup_definition' "
                "AND p.resource_id=d.definition_id) ORDER BY d.rowid DESC"
            ).fetchall(),
        )
        definitions = (
            SetupDefinitionRevision.model_validate_json(cast("str", row[0]))
            for row in rows
        )
        return tuple(item for item in definitions if item.purpose == "experiment")

    def save_definition(
        self, definition: SetupDefinitionRevision
    ) -> SetupDefinitionRevision:
        try:
            existing = self.definition(definition.id)
        except KeyError:
            existing = None
        if existing is not None:
            if existing.model_dump(exclude={"recorded_at"}) != definition.model_dump(
                exclude={"recorded_at"}
            ):
                raise ValueError(
                    "setup definition ID already has different content or provenance"
                )
            return existing
        self._connection.execute(
            "INSERT INTO setup_definitions(definition_id, record_json) VALUES (?, ?)",
            (definition.id, definition.model_dump_json()),
        )
        return definition

    def resolve(self, definition_id: str) -> SetupRevision:
        definition = self.definition(definition_id)
        devices = DeviceRepository(self._connection)
        views = [
            devices.view(binding.device_id)
            for binding in definition.definition.instruments
        ]
        if any(view.device.state != "available" for view in views):
            raise ValueError("setup references a retired device; update its bindings")
        snapshot = definition.definition.resolve(
            {view.device.id: view.revision for view in views}
        )
        resolution = SetupDeviceResolution(
            definition_id=definition.id,
            definition_hash=definition.definition.content_hash,
            devices=tuple(view.revision.ref for view in views),
        )
        content_hash = resolved_setup_hash(snapshot, resolution)
        return self.save_revision(
            SetupRevision(
                id=f"resolved:{content_hash.removeprefix('sha256:')}",
                content_hash=content_hash,
                setup=snapshot,
                resolution=resolution,
                actor=definition.actor,
                note=definition.note,
            )
        )

    def read_revision(self, revision_id: str) -> SetupRevision:
        row = cast(
            "sqlite3.Row | None",
            self._connection.execute(
                "SELECT record_json FROM setup_revisions WHERE revision_id = ?",
                (revision_id,),
            ).fetchone(),
        )
        if row is None:
            raise KeyError(revision_id)
        return SetupRevision.model_validate_json(cast("str", row[0]))

    def list_revisions(self) -> tuple[SetupRevision, ...]:
        rows = cast(
            "list[sqlite3.Row]",
            self._connection.execute(
                "SELECT record_json FROM setup_revisions s WHERE NOT EXISTS "
                "(SELECT 1 FROM practice_resources p WHERE p.kind='setup' "
                "AND p.resource_id=s.revision_id) ORDER BY s.rowid DESC"
            ).fetchall(),
        )
        return tuple(
            SetupRevision.model_validate_json(cast("str", row[0])) for row in rows
        )

    def save_revision(self, revision: SetupRevision) -> SetupRevision:
        try:
            existing = self.read_revision(revision.id)
        except KeyError:
            existing = None
        if existing is not None:
            if existing.model_dump(exclude={"recorded_at"}) != revision.model_dump(
                exclude={"recorded_at"}
            ):
                raise ValueError(
                    "setup revision ID already has different content or provenance"
                )
            return existing
        self._connection.execute(
            "INSERT INTO setup_revisions(revision_id, record_json) VALUES (?, ?)",
            (revision.id, revision.model_dump_json()),
        )
        return revision
