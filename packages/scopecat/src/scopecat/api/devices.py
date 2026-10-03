"""Device maintenance shared with the workbench; listing never connects hardware."""

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from scopecat.api.instruments import InstrumentSessionHandle
from scopecat.daemon.client import DaemonClient
from scopecat.daemon.device_views import DeviceView
from scopecat.daemon.wire import (
    DeviceProbeCommand,
    DeviceRenameCommand,
    DeviceRetireCommand,
    DeviceSaveCommand,
    InstrumentDriverProbeReceipt,
)
from scopecat.records.device import (
    DeviceConnection,
    DriverImplementationRef,
    RegisteredDevice,
)
from scopecat.records.driver_source import DriverSourceSelection, DriverSourceUpdate


@dataclass(frozen=True, slots=True)
class LabDeviceOperations:
    client: DaemonClient
    operator: str

    def list(self) -> tuple[DeviceView, ...]:
        return self.client.list_devices().items

    def drivers(self) -> tuple[DriverImplementationRef, ...]:
        return self.client.device_drivers().items

    def open(self, device_id: str) -> InstrumentSessionHandle:
        """Open a registered device with the same ownership as experiments."""
        setup = self.client.prepare_device_access(device_id)
        return InstrumentSessionHandle(
            client=self.client,
            actor=self.operator,
            setup=setup.ref,
            instrument_ids=(device_id,),
        )

    def driver_source(self) -> DriverSourceSelection | None:
        """Inspect the selected source without reading the development directory."""
        return self.client.driver_source()

    def update_driver_source(
        self,
        source_root: str | Path,
        *,
        operation_id: str | None = None,
        python: str | Path | None = None,
    ) -> DriverSourceSelection:
        """Capture local source and switch drivers once affected devices are idle."""
        current = self.driver_source()
        previous = None if current is None else current.request.operation_id
        if current is not None and current.request.operation_id == operation_id:
            previous = current.request.expected_previous
        return self.client.update_driver_source(
            DriverSourceUpdate(
                operation_id=operation_id or uuid4().hex,
                source_root=str(Path(source_root).expanduser().resolve()),
                python=str(Path(python).expanduser().absolute())
                if python is not None
                else None,
                expected_previous=previous,
                actor=self.operator,
            )
        )

    def test_connection(self, device: DeviceView) -> InstrumentDriverProbeReceipt:
        return self.client.test_device_connection(
            device.device.id,
            DeviceProbeCommand(
                expected_head=device.device.head,
                operation_id=uuid4().hex,
                actor=self.operator,
            ),
        )

    def register(
        self, *, label: str, connection: DeviceConnection, device_id: str | None = None
    ) -> DeviceView:
        return self.client.save_device(
            DeviceSaveCommand(
                device_id=device_id or uuid4().hex,
                label=label,
                revision_id=uuid4().hex,
                connection=connection,
                expected_head=None,
                actor=self.operator,
            )
        )

    def update(
        self, device: DeviceView, *, connection: DeviceConnection, note: str = ""
    ) -> DeviceView:
        """Requires drained owners and the exact head reviewed by the caller."""
        return self.client.save_device(
            DeviceSaveCommand(
                device_id=device.device.id,
                label=device.device.label,
                revision_id=uuid4().hex,
                connection=connection,
                expected_head=device.device.head,
                actor=self.operator,
                note=note,
            )
        )

    def rename(self, device_id: str, label: str) -> RegisteredDevice:
        return self.client.rename_device(device_id, DeviceRenameCommand(label=label))

    def retire(self, device: DeviceView) -> RegisteredDevice:
        return self.client.retire_device(
            device.device.id, DeviceRetireCommand(expected_head=device.device.head)
        )
