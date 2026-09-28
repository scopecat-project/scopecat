"""Explicit installed capability for tests using the signal instrument fixture."""

from scopecat.records.setup import SetupRevisionRef
from scopecat.sdk.instruments import InstrumentBackend
from scopecat_server.instruments.backend import LocalInstrumentBackendEndpoint
from scopecat_server.storage.sqlite.control_plane import SQLiteControlPlane

from scopecat_testkit.signal_instruments import (
    TestSignalInstrumentProvider,
)
from scopecat_testkit.signal_instruments import (
    signal_driver_catalog as signal_driver_catalog,
)


def signal_endpoint() -> LocalInstrumentBackendEndpoint:
    provider = TestSignalInstrumentProvider()
    return LocalInstrumentBackendEndpoint(
        InstrumentBackend(
            provider=provider,
            driver_catalog=signal_driver_catalog(provider.provider_id),
        )
    )


def seed_device_setup(
    control: SQLiteControlPlane, *, name: str, bindings: dict[str, str]
) -> SetupRevisionRef:
    """Provision explicit device references for control-plane-only storage tests."""
    from scopecat.records.config import (
        RoutingGraph,
        Topology,
        VirtualInstrumentConnection,
    )
    from scopecat.records.device import (
        DeviceConnection,
        DeviceConnectionRevision,
        DriverImplementationRef,
    )
    from scopecat.records.setup import (
        SetupDefinition,
        SetupDefinitionRevision,
        SetupInstrumentBinding,
    )
    from scopecat_server.storage.sqlite.devices import DeviceRepository
    from scopecat_server.storage.sqlite.setups import SQLiteSetupRepository

    with control.write_transaction() as connection:
        devices = DeviceRepository(connection)
        for device_id in set(bindings.values()):
            devices.save(
                label=device_id,
                expected_head=None,
                revision=DeviceConnectionRevision(
                    id=f"fixture:{device_id}",
                    device_id=device_id,
                    actor="test",
                    content=DeviceConnection(
                        driver=DriverImplementationRef(
                            provider_id="test-fixture",
                            driver_id="test-fixture",
                            artifact_hash="sha256:" + "0" * 64,
                        ),
                        connection=VirtualInstrumentConnection(),
                    ),
                ),
            )
        setups = SQLiteSetupRepository(connection)
        setups.save_definition(
            SetupDefinitionRevision(
                id=name,
                actor="test",
                definition=SetupDefinition(
                    topology=Topology(),
                    routing=RoutingGraph(),
                    domain_target=None,
                    instruments=tuple(
                        SetupInstrumentBinding(
                            id=alias,
                            device_id=device_id,
                            run_start="preserve",
                            success_action="release",
                            failure_action="abort_and_release",
                        )
                        for alias, device_id in bindings.items()
                    ),
                ),
            )
        )
        return setups.resolve(name).ref
