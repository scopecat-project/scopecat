"""Public source management uses real snapshots, workers and device leases."""

import shutil
import sqlite3
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient
from scopecat.api.devices import LabDeviceOperations
from scopecat.api.instruments import instrument
from scopecat.daemon.client import DaemonClient, DaemonConflictError
from scopecat.kernel.quantity import Quantity
from scopecat.records.config import VirtualInstrumentConnection
from scopecat.records.device import DeviceConnection
from scopecat.records.driver_source import (
    DriverSourceCollection,
    DriverSourceCommand,
    LocalDriverSource,
)
from scopecat_instruments.clients import RFOutputClient

from scopecat_server.errors import BackendConflict
from scopecat_server.instruments.backend import InstrumentBackendUnavailable
from scopecat_server.instruments.worker import SubprocessInstrumentBackendEndpoint
from scopecat_server.runtime import LocalDaemonRuntime
from scopecat_server.services import driver_sources
from scopecat_server.storage.sqlite.driver_sources import DriverSourceRepository


@pytest.fixture(autouse=True)
def isolated_locator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCOPECAT_LOCATOR_HOME", str(tmp_path / "locator"))


def _client(transport: TestClient) -> DaemonClient:
    def send(request: httpx2.Request) -> httpx2.Response:
        response = transport.request(
            request.method,
            request.url.raw_path.decode(),
            content=request.content,
            headers=dict(request.headers),
        )
        return httpx2.Response(
            response.status_code,
            content=response.content,
            headers=dict(response.headers),
        )

    return DaemonClient("http://testserver", transport=httpx2.MockTransport(send))


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "lab-driver"
    shutil.copytree(
        Path(__file__).parent / "fixtures/instrument_worker_project", source
    )
    (source / "scopecat.toml").write_text(
        '[lab]\ninstrument_backend = "worker_fixture.backend:create_backend"\n'
        '[authors]\nsource_roots = ["src"]\ndependencies = []\n'
    )
    return source


def test_simulated_device_and_multiple_sources_survive_restart(tmp_path: Path) -> None:
    project = tmp_path / "app"
    source = _source(tmp_path)
    with (
        LocalDaemonRuntime(project) as runtime,
        TestClient(runtime.app()) as transport,
        _client(transport) as client,
    ):
        devices = LabDeviceOperations(client, "operator")
        assert devices.driver_sources().collection.sources == ()
        command = DriverSourceCommand(
            operation_id="simulation",
            source_id="sim",
            builtin="simulation",
            actor="operator",
        )
        initial = devices.manage_driver_source(command)
        assert devices.manage_driver_source(command) == initial
        assert all(".virtual." in item.driver_id for item in devices.drivers())
        reference = next(
            item
            for item in devices.drivers()
            if item.driver_id == "scopecat.virtual.rf_source"
        )
        registered = devices.register(
            label="Simulated RF (no hardware)",
            device_id="rf",
            connection=DeviceConnection(
                driver=reference, connection=VirtualInstrumentConnection()
            ),
        )
        with devices.open("rf") as session:
            rf = session[instrument("rf", RFOutputClient)]
            assert rf.frequency.read() == Quantity(5e9, "Hz")
            rf.apply(frequency=Quantity(5.1, "GHz"))
            assert rf.frequency.read() == Quantity(5.1e9, "Hz")
            with pytest.raises(DaemonConflictError, match="in use"):
                devices.manage_driver_source(
                    DriverSourceCommand(
                        operation_id="busy",
                        expected_previous="simulation",
                        source_id="lab",
                        source_root=str(source),
                        actor="operator",
                    )
                )
        added = devices.manage_driver_source(
            DriverSourceCommand(
                operation_id="add",
                expected_previous="simulation",
                source_id="lab",
                source_root=str(source),
                actor="operator",
            )
        )
        assert len(added.sources) == 2
        assert (
            runtime.application.devices.get("rf").device.head == registered.device.head
        )
        assert runtime.application.devices.driver_ref(reference.driver_id) == reference
        with devices.open("rf") as session:
            assert session[
                instrument("rf", RFOutputClient)
            ].frequency.read() == Quantity(5e9, "Hz")
        # Duplicate driver IDs cannot rebind devices or historical references.
        with pytest.raises(DaemonConflictError, match="driver ID conflict"):
            devices.manage_driver_source(
                DriverSourceCommand(
                    operation_id="conflict",
                    expected_previous="add",
                    source_id="duplicate",
                    builtin="simulation",
                    actor="operator",
                )
            )
        assert devices.driver_sources().collection == added
        with pytest.raises(DaemonConflictError, match="does not provide"):
            devices.manage_driver_source(
                DriverSourceCommand(
                    operation_id="disable-in-use",
                    expected_previous="add",
                    source_id="sim",
                    action="disable",
                    actor="operator",
                )
            )
        disabled = devices.manage_driver_source(
            DriverSourceCommand(
                operation_id="disable",
                expected_previous="add",
                source_id="lab",
                action="disable",
                actor="operator",
            )
        )
        assert not disabled.sources[1].enabled
    shutil.rmtree(source)
    with LocalDaemonRuntime(project) as runtime:
        service = runtime.application.driver_sources
        assert service.sources().errors == {}
        enabled = service.manage(
            DriverSourceCommand(
                operation_id="enable",
                expected_previous="disable",
                source_id="lab",
                action="enable",
                actor="operator",
            )
        )
        assert enabled.sources[1].enabled
        assert runtime.application.devices.driver_ref(reference.driver_id) == reference
        assert (
            runtime.application.devices.get("rf").device.head == registered.device.head
        )


def test_failure_and_source_scoped_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "app"
    source = _source(tmp_path)
    with LocalDaemonRuntime(project) as runtime:
        service = runtime.application.driver_sources
        first = service.manage(
            DriverSourceCommand(
                operation_id="lab",
                source_id="lab",
                source_root=str(source),
                actor="operator",
            )
        )
        original = runtime.application.devices.endpoint
        (source / "src/worker_fixture/backend.py").write_text("invalid python !")
        with pytest.raises(InstrumentBackendUnavailable, match="failed to start"):
            service.manage(
                DriverSourceCommand(
                    operation_id="broken",
                    expected_previous="lab",
                    source_id="lab",
                    source_root=str(source),
                    actor="operator",
                )
            )
        assert service.sources().collection == first
        assert runtime.application.devices.endpoint is original
        service.manage(
            DriverSourceCommand(
                operation_id="sim",
                expected_previous="lab",
                source_id="sim",
                builtin="simulation",
                actor="operator",
            )
        )
    restore = driver_sources._restore_entry

    def unavailable(
        root: Path, repository: DriverSourceRepository, entry: LocalDriverSource
    ) -> SubprocessInstrumentBackendEndpoint:
        if entry.id == "lab":
            raise ValueError("missing vendor dependency")
        return restore(root, repository, entry)

    monkeypatch.setattr(driver_sources, "_restore_entry", unavailable)
    with LocalDaemonRuntime(project) as runtime:
        state = runtime.application.driver_sources.sources()
        assert state.errors == {"lab": "missing vendor dependency"}
        assert runtime.application.devices.driver_ref("scopecat.virtual.rf_source")
        assert runtime.application.instruments.healthy
        with pytest.raises(BackendConflict, match="changed"):
            runtime.application.driver_sources.manage(
                DriverSourceCommand(
                    operation_id="stale",
                    source_id="sim",
                    action="disable",
                    actor="operator",
                )
            )


def test_legacy_identity_and_public_sources_publish_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "app"
    source = _source(tmp_path)
    with (
        LocalDaemonRuntime(project) as runtime,
        TestClient(runtime.app()) as transport,
        _client(transport) as client,
    ):
        devices = LabDeviceOperations(client, "operator")
        legacy = devices.update_driver_source(source)
        reference = devices.drivers()[0]
        registered = devices.register(
            label="Legacy virtual device",
            device_id="legacy-device",
            connection=DeviceConnection(
                driver=reference, connection=VirtualInstrumentConnection()
            ),
        )
        service = runtime.application.driver_sources
        assert service.sources().collection.sources[0].legacy_identity
        command = DriverSourceCommand(
            operation_id="public",
            source_id="public",
            builtin="public",
            actor="operator",
        )
        old_endpoint = runtime.application.devices.endpoint
        publish = service.repository.publish_collection

        def fail(
            connection: sqlite3.Connection,
            collection: DriverSourceCollection,
            bundles: dict[str, str],
        ) -> None:
            publish(connection, collection, bundles)
            raise ValueError("publication rolled back")

        with monkeypatch.context() as patch:
            patch.setattr(service.repository, "publish_collection", fail)
            with pytest.raises(BackendConflict, match="publication rolled back"):
                service.manage(command)
        assert runtime.application.devices.endpoint is old_endpoint
        assert service.repository.collection() is None
        assert (
            runtime.application.devices.get("legacy-device").device.head
            == registered.device.head
        )
        collected = service.manage(command)
        assert collected.provider_id == reference.provider_id
        assert runtime.application.devices.driver_ref(reference.driver_id) == reference
        assert (
            runtime.application.devices.get("legacy-device").device.head
            == registered.device.head
        )
        assert service.current() == legacy
        with pytest.raises(BackendConflict, match="different intent"):
            service.manage(command.model_copy(update={"source_id": "other"}))
        assert not any(
            ".virtual." in driver for driver in collected.sources[1].driver_ids
        )
    shutil.rmtree(source)
    with LocalDaemonRuntime(project) as runtime:
        assert runtime.application.driver_sources.sources().errors == {}
        assert runtime.application.devices.driver_ref(reference.driver_id) == reference
        assert (
            runtime.application.devices.get("legacy-device").device.head
            == registered.device.head
        )
