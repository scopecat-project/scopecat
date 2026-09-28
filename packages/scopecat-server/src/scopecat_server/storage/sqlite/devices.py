"""Transaction-local device writes and exact connection reads.

Lifecycle quiescence is enforced by the service before calling these writes.
Aliases reserve current access paths; immutable revisions retain historical paths.
"""

from __future__ import annotations

import sqlite3
from typing import cast

from scopecat.daemon.device_views import DeviceConnectionTest, DeviceView
from scopecat.records.device import (
    DeviceConnectionRevision,
    DeviceRevisionRef,
    RegisteredDevice,
    connection_access_alias,
)


class DeviceRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def get(self, device_id: str) -> RegisteredDevice:
        row = cast(
            "sqlite3.Row | None",
            self.connection.execute(
                "SELECT record_json FROM devices WHERE device_id = ?", (device_id,)
            ).fetchone(),
        )
        if row is None:
            raise KeyError(device_id)
        return RegisteredDevice.model_validate_json(cast("str", row[0]))

    def revision(self, revision_id: str) -> DeviceConnectionRevision:
        row = cast(
            "sqlite3.Row | None",
            self.connection.execute(
                "SELECT record_json FROM device_connection_revisions "
                "WHERE revision_id = ?",
                (revision_id,),
            ).fetchone(),
        )
        if row is None:
            raise KeyError(revision_id)
        return DeviceConnectionRevision.model_validate_json(cast("str", row[0]))

    def view(self, device_id: str) -> DeviceView:
        device = self.get(device_id)
        row = cast(
            "sqlite3.Row | None",
            self.connection.execute(
                "SELECT record_json FROM device_connection_tests "
                "WHERE revision_id = ? ORDER BY rowid DESC LIMIT 1",
                (device.head.revision_id,),
            ).fetchone(),
        )
        return DeviceView(
            device=device,
            revision=self.revision(device.head.revision_id),
            last_connection_test=None
            if row is None
            else DeviceConnectionTest.model_validate_json(cast("str", row[0])),
        )

    def connection_test(self, operation_id: str) -> DeviceConnectionTest | None:
        row = cast(
            "sqlite3.Row | None",
            self.connection.execute(
                "SELECT record_json FROM device_connection_tests "
                "WHERE operation_id = ?",
                (operation_id,),
            ).fetchone(),
        )
        return (
            None
            if row is None
            else DeviceConnectionTest.model_validate_json(cast("str", row[0]))
        )

    def save_connection_test(
        self, result: DeviceConnectionTest
    ) -> DeviceConnectionTest:
        prior = self.connection_test(result.operation_id)
        if prior is not None:
            if prior.revision != result.revision or prior.actor != result.actor:
                raise ValueError("connection test operation has different intent")
            return prior
        self.connection.execute(
            "INSERT INTO device_connection_tests "
            "(operation_id, revision_id, record_json) VALUES (?, ?, ?)",
            (
                result.operation_id,
                result.revision.revision_id,
                result.model_dump_json(),
            ),
        )
        return result

    def list(self) -> tuple[DeviceView, ...]:
        rows = cast(
            "list[sqlite3.Row]",
            self.connection.execute(
                "SELECT device_id FROM devices ORDER BY rowid"
            ).fetchall(),
        )
        return tuple(self.view(cast("str", row[0])) for row in rows)

    def alias_owner(self, alias: str) -> str | None:
        row = cast(
            "sqlite3.Row | None",
            self.connection.execute(
                "SELECT device_id FROM device_access_aliases WHERE alias = ?", (alias,)
            ).fetchone(),
        )
        return None if row is None else cast("str", row[0])

    def validate_save(
        self,
        *,
        label: str,
        revision: DeviceConnectionRevision,
        expected_head: DeviceRevisionRef | None,
    ) -> tuple[DeviceView, bool, set[str]]:
        try:
            current = self.get(revision.device_id)
        except KeyError:
            current = None
        try:
            prior = self.revision(revision.id)
        except KeyError:
            prior = None
        if revision.previous != expected_head:
            raise ValueError("connection predecessor differs from the reviewed head")
        if prior is not None:
            if prior.model_dump(exclude={"recorded_at"}) != revision.model_dump(
                exclude={"recorded_at"}
            ):
                raise ValueError(
                    "device revision ID already has different content or provenance"
                )
            if current is not None and current.head == prior.ref:
                return DeviceView(device=current, revision=prior), False, set()
        if (None if current is None else current.head) != expected_head:
            raise ValueError(
                "device connection changed; reload and review before saving"
            )
        if current is not None and current.state == "retired":
            raise ValueError("retired device cannot accept a new connection")
        aliases = set(revision.content.access_aliases)
        address = connection_access_alias(revision.content.connection)
        if address is not None:
            aliases.add(address)
        if revision.content.connection.kind == "driver_managed" and not aliases:
            raise ValueError(
                "driver-managed hardware requires an explicit physical access alias"
            )
        for alias in aliases:
            owner = self.alias_owner(alias)
            if owner is not None and owner != revision.device_id:
                raise ValueError(
                    f"access alias {alias} belongs to device {owner}; use that device"
                )
        device = RegisteredDevice(id=revision.device_id, label=label, head=revision.ref)
        return (
            DeviceView(device=device, revision=prior or revision),
            prior is None,
            aliases,
        )

    def save(
        self,
        *,
        label: str,
        revision: DeviceConnectionRevision,
        expected_head: DeviceRevisionRef | None,
    ) -> DeviceView:
        view, new_revision, aliases = self.validate_save(
            label=label, revision=revision, expected_head=expected_head
        )
        if not new_revision:
            return view
        device = view.device
        self.connection.execute(
            "INSERT INTO devices(device_id, record_json) VALUES (?, ?) "
            "ON CONFLICT(device_id) DO UPDATE SET record_json = excluded.record_json",
            (device.id, device.model_dump_json()),
        )
        self.connection.execute(
            "INSERT INTO device_connection_revisions"
            "(revision_id, device_id, record_json) VALUES (?, ?, ?)",
            (revision.id, device.id, revision.model_dump_json()),
        )
        self.connection.execute(
            "DELETE FROM device_access_aliases WHERE device_id = ?", (device.id,)
        )
        for alias in sorted(aliases):
            self.connection.execute(
                "INSERT OR IGNORE INTO device_access_aliases"
                "(alias, device_id) VALUES (?, ?)",
                (alias, device.id),
            )
        return view

    def rename(self, device_id: str, label: str) -> RegisteredDevice:
        current = self.get(device_id)
        updated = RegisteredDevice(
            id=current.id, label=label, state=current.state, head=current.head
        )
        self._update(updated)
        return updated

    def retire(
        self, device_id: str, expected_head: DeviceRevisionRef
    ) -> RegisteredDevice:
        current = self.get(device_id)
        if current.head != expected_head:
            raise ValueError("device connection changed; reload before retiring")
        retired = current.model_copy(update={"state": "retired"})
        self._update(retired)
        self.connection.execute(
            "DELETE FROM device_access_aliases WHERE device_id = ?", (device_id,)
        )
        return retired

    def require_current(self, references: tuple[DeviceRevisionRef, ...]) -> None:
        for ref in references:
            current = self.get(ref.device_id)
            if current.state != "available" or current.head != ref:
                raise ValueError(
                    f"device {current.label} changed; prepare the request again"
                )

    def _update(self, device: RegisteredDevice) -> None:
        self.connection.execute(
            "UPDATE devices SET record_json = ? WHERE device_id = ?",
            (device.model_dump_json(), device.id),
        )
