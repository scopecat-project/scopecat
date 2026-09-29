"""Application devices share ownership across setup aliases and survive reopening."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from scopecat.daemon.wire import InstrumentSessionOpenCommand, SetupSaveCommand
from scopecat.project import load_project
from scopecat.records.config import RoutingGraph, Topology
from scopecat.records.setup import (
    SetupDefinition,
    SetupInstrumentBinding,
    SetupRevision,
)
from scopecat.sdk.instruments import (
    DriverCatalog,
    DriverConnectionSpec,
    DriverSpec,
    InstrumentBackend,
)
from scopecat_testkit.signal_instruments import TestSignalInstrumentProvider

from scopecat_server.errors import BackendConflict
from scopecat_server.instruments.actors import InstrumentActorRetirement
from scopecat_server.instruments.backend import (
    InstrumentHandle,
    LocalInstrumentBackendEndpoint,
)
from scopecat_server.runtime import LocalDaemonRuntime
from scopecat_server.snapshots import create_snapshot, restore_snapshot
from scopecat_server.storage.sqlite.devices import DeviceRepository


def _endpoint() -> LocalInstrumentBackendEndpoint:
    provider = TestSignalInstrumentProvider()
    return LocalInstrumentBackendEndpoint(
        InstrumentBackend(
            provider=provider,
            driver_catalog=DriverCatalog(
                provider_id=provider.provider_id,
                drivers=(
                    DriverSpec(
                        driver_id="tests.signal_instrument",
                        implementation_version="v0",
                        label="Signal",
                        connections=(
                            DriverConnectionSpec(
                                kind="virtual",
                                options_schema={
                                    "type": "object",
                                    "properties": {"gain": {"type": "number"}},
                                },
                            ),
                        ),
                    ),
                ),
            ),
        )
    )


def _register(client: TestClient, device_id: str = "signal") -> dict[str, Any]:
    driver = client.get("/api/v1/devices/drivers").json()["items"][0]
    response = client.post(
        "/api/v1/devices",
        json={
            "device_id": device_id,
            "label": "Signal source",
            "revision_id": f"{device_id}-v1",
            "connection": {"driver": driver, "connection": {"kind": "virtual"}},
            "expected_head": None,
            "actor": "operator",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_backend_replacement_updates_devices_without_restarting_application(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous = _endpoint()
    replacement = _endpoint()
    monkeypatch.setattr(replacement, "_artifact_hash", "sha256:" + "0" * 64)
    with (
        LocalDaemonRuntime(tmp_path, instrument_endpoint=previous) as runtime,
        TestClient(runtime.app()) as client,
    ):
        registered = _register(client)
        devices = runtime.application.devices
        instruments = runtime.application.instruments
        setup = devices.access_setup("signal")
        first = instruments.open_session(
            InstrumentSessionOpenCommand(
                setup=setup.ref,
                actor="operator",
                operation_id="before-update",
                instrument_ids=("signal",),
            )
        )
        instruments.close_session(first.session_id)
        [updated] = devices.replace_backend(
            replacement, instruments, actor="maintainer"
        )
        assert not previous.healthy
        assert replacement.healthy
        assert devices.endpoint is replacement
        assert updated.revision.previous == setup.resolution.devices[0]
        assert (
            updated.revision.content.driver.artifact_hash == replacement.artifact_hash
        )
        assert updated.revision.content.driver.artifact_hash != previous.artifact_hash
        assert updated.revision.content.model_dump(mode="json", exclude={"driver"}) == {
            key: value
            for key, value in registered["revision"]["content"].items()
            if key != "driver"
        }
        assert updated.revision.actor == "maintainer"
        assert updated.device.head != setup.resolution.devices[0]
        assert updated.availability == "idle"
        with pytest.raises(BackendConflict):
            instruments.open_session(
                InstrumentSessionOpenCommand(
                    setup=setup.ref,
                    actor="operator",
                    operation_id="stale-setup",
                    instrument_ids=("signal",),
                )
            )
        refreshed = devices.access_setup("signal")
        second = instruments.open_session(
            InstrumentSessionOpenCommand(
                setup=refreshed.ref,
                actor="operator",
                operation_id="after-update",
                instrument_ids=("signal",),
            )
        )
        instruments.close_session(second.session_id)
        assert client.get("/api/v1/devices").status_code == 200
    assert not replacement.healthy


def test_backend_replacement_busy_device_keeps_other_connections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    previous = _endpoint()
    replacement = _endpoint()
    disconnected: list[InstrumentHandle] = []
    original = previous.disconnect

    def track_disconnect(handle: InstrumentHandle) -> None:
        disconnected.append(handle)
        original(handle)

    monkeypatch.setattr(previous, "disconnect", track_disconnect)
    with (
        LocalDaemonRuntime(tmp_path, instrument_endpoint=previous) as runtime,
        TestClient(runtime.app()) as client,
    ):
        devices = runtime.application.devices
        instruments = runtime.application.instruments
        for name in ("signal", "busy"):
            _register(client, name)
            setup = devices.access_setup(name)
            opened = instruments.open_session(
                InstrumentSessionOpenCommand(
                    setup=setup.ref,
                    actor="operator",
                    operation_id=f"open-{name}",
                    instrument_ids=(name,),
                )
            )
            if name == "signal":
                instruments.close_session(opened.session_id)
        before = tuple(view.device.head for view in devices.list())
        with pytest.raises(BackendConflict, match="device is in use"):
            devices.replace_backend(replacement, instruments, actor="maintainer")
        assert disconnected == []
        assert previous.healthy
        assert not replacement.healthy
        assert devices.endpoint is previous
        assert tuple(view.device.head for view in devices.list()) == before
        assert devices.list()[0].availability == "idle"
        instruments.close_session(opened.session_id)


def test_backend_publication_failure_rolls_back_all_device_heads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    previous = _endpoint()
    replacement = _endpoint()
    with (
        LocalDaemonRuntime(tmp_path, instrument_endpoint=previous) as runtime,
        TestClient(runtime.app()) as client,
    ):
        for name in ("signal", "other"):
            _register(client, name)
        devices = runtime.application.devices
        instruments = runtime.application.instruments
        before = tuple(view.device.head for view in devices.list())
        original = DeviceRepository.save

        def fail_second(repository: DeviceRepository, **kwargs: Any) -> Any:
            if kwargs["revision"].device_id == "other":
                raise ValueError("publication interrupted")
            return original(repository, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(DeviceRepository, "save", fail_second)
            with pytest.raises(BackendConflict, match="publication interrupted"):
                devices.replace_backend(replacement, instruments, actor="maintainer")
        assert tuple(view.device.head for view in devices.list()) == before
        assert all(view.availability == "idle" for view in devices.list())
        assert devices.endpoint is previous
        assert previous.healthy
        assert not replacement.healthy
        opened = instruments.open_session(
            InstrumentSessionOpenCommand(
                setup=devices.access_setup("signal").ref,
                actor="operator",
                operation_id="after-failed-publication",
                instrument_ids=("signal",),
            )
        )
        instruments.close_session(opened.session_id)


@pytest.mark.parametrize("failure", ["missing_driver", "disconnect"])
def test_failed_backend_replacement_preserves_device_revision_and_attention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    previous = _endpoint()
    replacement = _endpoint()
    disconnected: list[InstrumentHandle] = []
    original = previous.disconnect

    def disconnect(handle: InstrumentHandle) -> None:
        disconnected.append(handle)
        if failure == "disconnect":
            raise RuntimeError("transport did not confirm disconnect")
        original(handle)

    if failure == "missing_driver":
        monkeypatch.setattr(
            replacement,
            "_driver_catalog",
            replacement.driver_catalog.model_copy(update={"drivers": ()}),
        )
    with (
        LocalDaemonRuntime(tmp_path, instrument_endpoint=previous) as runtime,
        TestClient(runtime.app()) as client,
    ):
        _register(client)
        devices = runtime.application.devices
        instruments = runtime.application.instruments
        setup = devices.access_setup("signal")
        opened = instruments.open_session(
            InstrumentSessionOpenCommand(
                setup=setup.ref,
                actor="operator",
                operation_id="open",
                instrument_ids=("signal",),
            )
        )
        instruments.close_session(opened.session_id)
        monkeypatch.setattr(previous, "disconnect", disconnect)
        with pytest.raises(
            BackendConflict,
            match=(
                "does not provide driver"
                if failure == "missing_driver"
                else "could not release existing connections"
            ),
        ):
            devices.replace_backend(replacement, instruments, actor="maintainer")
        assert devices.endpoint is previous
        assert previous.healthy
        assert not replacement.healthy
        [view] = devices.list()
        assert view.device.head == setup.resolution.devices[0]
        assert len(disconnected) == (1 if failure == "disconnect" else 0)
        assert view.availability == (
            "quarantined" if failure == "disconnect" else "idle"
        )
        monkeypatch.setattr(previous, "disconnect", original)
    with LocalDaemonRuntime(tmp_path, instrument_endpoint=_endpoint()) as reopened:
        [retained] = reopened.application.devices.list()
        assert retained.device.head == view.device.head
        assert retained.availability == view.availability


@pytest.mark.parametrize("device_id", ["signal", "rack-a/signal"])
def test_connection_test_is_retained_for_exact_device_revision(
    tmp_path: Path, device_id: str
) -> None:
    with (
        LocalDaemonRuntime(tmp_path, instrument_endpoint=_endpoint()) as runtime,
        TestClient(runtime.app()) as client,
    ):
        registered = _register(client, device_id)
        assert registered["last_connection_test"] is None
        command = {
            "expected_head": registered["device"]["head"],
            "operation_id": "connection-test",
            "actor": "operator",
        }
        tested = client.post(
            f"/api/v1/devices/{device_id}/connection-tests", json=command
        )
        assert tested.status_code == 200, tested.text
        assert tested.json()["status"] == "connected"
        assert (
            client.post(
                f"/api/v1/devices/{device_id}/connection-tests", json=command
            ).json()
            == tested.json()
        )
        view = runtime.application.devices.get(device_id)
        assert view.last_connection_test is not None
        assert view.last_connection_test.revision == view.device.head
        assert view.last_connection_test.description is not None
        assert view.last_connection_test.error is None
    with (
        LocalDaemonRuntime(tmp_path, instrument_endpoint=_endpoint()) as reopened,
        TestClient(reopened.app()) as client,
    ):
        assert (
            reopened.application.devices.get(device_id).last_connection_test
            == view.last_connection_test
        )
        edited = client.post(
            "/api/v1/devices",
            json={
                "device_id": device_id,
                "label": "Signal",
                "revision_id": "connection-v2",
                "expected_head": registered["device"]["head"],
                "actor": "operator",
                "connection": {
                    **registered["revision"]["content"],
                    "access_aliases": ["bench:signal"],
                },
            },
        )
        assert edited.status_code == 200, edited.text
        assert reopened.application.devices.get(device_id).last_connection_test is None


def test_unregistered_connection_cannot_bypass_device_ownership(tmp_path: Path) -> None:
    with (
        LocalDaemonRuntime(tmp_path, instrument_endpoint=_endpoint()) as runtime,
        TestClient(runtime.app()) as client,
    ):
        _register(client)
        setup = _setup(client, "bench")
        response = client.post(
            "/api/v1/instrument-sessions",
            json={
                "setup": setup.ref.model_dump(mode="json"),
                "actor": "operator",
                "operation_id": "bypass",
                "instrument_ids": ["unregistered"],
                "temporary_bindings": [
                    {
                        "id": "unregistered",
                        "driver_id": "tests.signal_instrument",
                        "connection": {"kind": "virtual"},
                    }
                ],
            },
        )
        assert response.status_code == 422, response.text
        assert runtime.application.devices.list()[0].availability == "idle"


def test_invalid_connection_edit_does_not_disconnect_resident_device(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    endpoint = _endpoint()
    with (
        LocalDaemonRuntime(tmp_path, instrument_endpoint=endpoint) as runtime,
        TestClient(runtime.app()) as client,
    ):
        device = _register(client)
        other = _register(client, "other")
        alias = {
            "device_id": "other",
            "label": "Other",
            "revision_id": "other-alias",
            "expected_head": other["device"]["head"],
            "actor": "operator",
            "connection": {
                **other["revision"]["content"],
                "access_aliases": ["bench:reserved"],
            },
        }
        assert client.post("/api/v1/devices", json=alias).status_code == 200
        setup = runtime.application.devices.access_setup("signal")
        session = runtime.application.instruments.open_session(
            InstrumentSessionOpenCommand(
                setup=setup.ref,
                actor="operator",
                operation_id="open",
                instrument_ids=("signal",),
            )
        )
        runtime.application.instruments.close_session(session.session_id)
        disconnected = []
        original = endpoint.disconnect

        def track_disconnect(handle: InstrumentHandle) -> None:
            disconnected.append(handle)
            original(handle)

        monkeypatch.setattr(endpoint, "disconnect", track_disconnect)
        command = {
            "device_id": "signal",
            "label": "Signal",
            "revision_id": "invalid",
            "expected_head": device["device"]["head"],
            "actor": "operator",
            "connection": {
                **device["revision"]["content"],
                "access_aliases": ["bench:reserved"],
            },
        }
        assert client.post("/api/v1/devices", json=command).status_code == 409
        command["connection"] = {
            **device["revision"]["content"],
            "connection": {"kind": "serial", "port": "COM4"},
        }
        response = client.post("/api/v1/devices", json=command)
        assert response.status_code == 409, response.text
        assert "does not support serial" in response.text
        command["connection"] = {
            **device["revision"]["content"],
            "connection": {"kind": "virtual", "options": {"gain": "not a number"}},
        }
        response = client.post("/api/v1/devices", json=command)
        assert response.status_code == 409, response.text
        assert "invalid connection options" in response.text
        assert disconnected == []


def _setup(client: TestClient, name: str, alias: str = "source") -> SetupRevision:
    response = client.post(
        "/api/v1/setup/revisions",
        json=SetupSaveCommand(
            revision_id=name,
            setup=SetupDefinition(
                topology=Topology(),
                instruments=(
                    SetupInstrumentBinding(
                        id=alias,
                        device_id="signal",
                        run_start="preserve",
                        success_action="release",
                        failure_action="abort_and_release",
                    ),
                ),
                routing=RoutingGraph(),
                domain_target=None,
            ),
            actor="operator",
        ).model_dump(mode="json"),
    )
    assert response.status_code == 200, response.text
    return SetupRevision.model_validate(response.json())


def test_device_edit_invalidates_resolution_but_preserves_evidence(
    tmp_path: Path,
) -> None:
    with (
        LocalDaemonRuntime(tmp_path, instrument_endpoint=_endpoint()) as runtime,
        TestClient(runtime.app()) as client,
    ):
        device = _register(client)
        first = _setup(client, "bench-a")
        second = _setup(client, "bench-b", "readout")
        assert (
            first.setup.instrument_registry.instruments[0].exclusivity_key
            == second.setup.instrument_registry.instruments[0].exclusivity_key
            == "signal"
        )
        update = {
            "device_id": "signal",
            "label": "Signal source",
            "revision_id": "signal-v2",
            "connection": {
                **device["revision"]["content"],
                "connection": {"kind": "virtual", "options": {"gain": 2}},
            },
            "expected_head": device["device"]["head"],
            "actor": "operator",
        }
        assert client.post("/api/v1/devices", json=update).status_code == 200
        assert (
            client.post(
                "/api/v1/devices", json={**update, "revision_id": "stale"}
            ).status_code
            == 409
        )
        newer = client.post("/api/v1/setup/resolutions/bench-a")
        assert newer.status_code == 200, newer.text
        resolved = SetupRevision.model_validate(newer.json())
        assert resolved.ref != first.ref
        assert runtime.application.setup.get(first.id) == first
        with pytest.raises(BackendConflict, match="changed; prepare"):
            runtime.application.instruments.open_session(
                InstrumentSessionOpenCommand(
                    setup=first.ref,
                    instrument_ids=("source",),
                    actor="operator",
                    operation_id="stale",
                )
            )
    with LocalDaemonRuntime(tmp_path, instrument_endpoint=_endpoint()) as reopened:
        assert (
            reopened.application.devices.get("signal").device.head.revision_id
            == "signal-v2"
        )
        assert reopened.application.setup.get(first.id) == first
        assert reopened.application.setup.resolve("bench-a").ref == resolved.ref


def test_device_aliases_share_resident_connection_and_maintenance_fence(
    tmp_path: Path,
) -> None:
    endpoint = _endpoint()
    with (
        LocalDaemonRuntime(tmp_path, instrument_endpoint=endpoint) as runtime,
        TestClient(runtime.app()) as client,
    ):
        device = _register(client)
        first = _setup(client, "bench-a")
        second = _setup(client, "bench-b", "readout")
        instruments = runtime.application.instruments
        session = instruments.open_session(
            InstrumentSessionOpenCommand(
                setup=first.ref,
                instrument_ids=("source",),
                actor="alice",
                operation_id="alice",
            )
        )
        handles = set(endpoint._connections)
        with pytest.raises(BackendConflict, match=r"busy|owned"):
            instruments.open_session(
                InstrumentSessionOpenCommand(
                    setup=second.ref,
                    instrument_ids=("readout",),
                    actor="bob",
                    operation_id="bob",
                )
            )
        blocked = client.post(
            "/api/v1/devices/signal/retirement",
            json={"expected_head": device["device"]["head"]},
        )
        assert blocked.status_code == 409, blocked.text
        instruments.close_session(session.session_id)
        next_session = instruments.open_session(
            InstrumentSessionOpenCommand(
                setup=second.ref,
                instrument_ids=("readout",),
                actor="bob",
                operation_id="bob",
            )
        )
        # One live backend handle even though the experiment alias has changed.
        assert set(endpoint._connections) == handles
        instruments.close_session(next_session.session_id)
        assert (
            client.post(
                "/api/v1/devices/signal/retirement",
                json={"expected_head": device["device"]["head"]},
            ).status_code
            == 200
        )
        assert len(endpoint._connections) == 0


def test_connection_retirement_failure_keeps_attention_across_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    endpoint = _endpoint()
    with (
        LocalDaemonRuntime(tmp_path, instrument_endpoint=endpoint) as runtime,
        TestClient(runtime.app()) as client,
    ):
        device = _register(client)
        setup = runtime.application.devices.access_setup("signal")
        opened = runtime.application.instruments.open_session(
            InstrumentSessionOpenCommand(
                setup=setup.ref,
                instrument_ids=("signal",),
                actor="operator",
                operation_id="open",
            )
        )
        runtime.application.instruments.close_session(opened.session_id)
        original = endpoint.disconnect

        def fail_disconnect(*_args: object) -> None:
            raise RuntimeError("transport did not confirm disconnect")

        monkeypatch.setattr(endpoint, "disconnect", fail_disconnect)
        failed = client.post(
            "/api/v1/devices/signal/retirement",
            json={"expected_head": device["device"]["head"]},
        )
        assert failed.status_code == 409, failed.text
        view = runtime.application.devices.list()[0]
        assert view.availability == "quarantined"
        assert view.device.state == "available"
        assert view.owner_id is not None
        owner = view.owner_id
        with pytest.raises(BackendConflict, match="restart the application"):
            runtime.application.instruments.resolve_attention(owner)
        monkeypatch.setattr(endpoint, "disconnect", original)
    with LocalDaemonRuntime(tmp_path, instrument_endpoint=_endpoint()) as reopened:
        assert reopened.application.devices.list()[0].availability == "quarantined"
        reopened.application.instruments.resolve_attention(owner)
        assert reopened.application.devices.list()[0].availability == "idle"
        reopened.application.devices.retire(
            "signal", reopened.application.devices.get("signal").device.head
        )


def test_maintenance_cannot_commit_after_its_session_expires(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with (
        LocalDaemonRuntime(tmp_path, instrument_endpoint=_endpoint()) as runtime,
        TestClient(runtime.app()) as client,
    ):
        device = _register(client)
        control = runtime.application.executor._control
        retire = InstrumentActorRetirement.retire_idle

        def expire_after_disconnect(retirement: InstrumentActorRetirement) -> None:
            retire(retirement)
            [session] = control.list_instrument_sessions()
            control.expire_instrument_session(
                session.session_id, at=datetime.now(tz=UTC) + timedelta(minutes=6)
            )

        monkeypatch.setattr(
            InstrumentActorRetirement, "retire_idle", expire_after_disconnect
        )
        response = client.post(
            "/api/v1/devices/signal/retirement",
            json={"expected_head": device["device"]["head"]},
        )
        assert response.status_code == 409, response.text
        assert "session is not active" in response.text
        assert runtime.application.devices.get("signal").device.state == "available"


def test_declared_alias_cannot_create_another_device_owner(tmp_path: Path) -> None:
    with (
        LocalDaemonRuntime(tmp_path, instrument_endpoint=_endpoint()) as runtime,
        TestClient(runtime.app()) as client,
    ):
        device = _register(client)
        content = {**device["revision"]["content"], "access_aliases": ["serial:COM4"]}
        command = {
            "device_id": "signal",
            "label": "Signal source",
            "revision_id": "signal-alias",
            "connection": content,
            "expected_head": device["device"]["head"],
            "actor": "operator",
        }
        saved = client.post("/api/v1/devices", json=command)
        assert saved.status_code == 200, saved.text
        assert client.post("/api/v1/devices", json=command).json() == saved.json()
        duplicate = client.post(
            "/api/v1/devices",
            json={
                **command,
                "device_id": "second",
                "revision_id": "second-v1",
                "expected_head": None,
            },
        )
        assert duplicate.status_code == 409, duplicate.text
        assert "belongs to device signal" in duplicate.text
        assert len(runtime.application.devices.list()) == 1
        snapshot = _setup(client, "before-retirement")
        retired = client.post(
            "/api/v1/devices/signal/retirement",
            json={"expected_head": saved.json()["device"]["head"]},
        )
        assert retired.status_code == 200, retired.text
        replacement = client.post(
            "/api/v1/devices",
            json={
                **command,
                "device_id": "second",
                "revision_id": "second-v1",
                "expected_head": None,
            },
        )
        assert replacement.status_code == 200, replacement.text
        with pytest.raises(BackendConflict, match="changed; prepare"):
            runtime.application.setup.require_available(snapshot.ref)
        assert runtime.application.setup.get(snapshot.id) == snapshot


def test_current_format_backup_retains_device_and_setup_ownership(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "scopecat.toml").write_text("[lab]\n", encoding="utf-8")
    with (
        LocalDaemonRuntime(source, instrument_endpoint=_endpoint()) as runtime,
        TestClient(runtime.app()) as client,
    ):
        registered = _register(client)
        setup = _setup(client, "bench")
        tested = client.post(
            "/api/v1/devices/signal/connection-tests",
            json={
                "expected_head": registered["device"]["head"],
                "operation_id": "backup-connection-test",
                "actor": "operator",
            },
        )
        assert tested.status_code == 200, tested.text
        device = runtime.application.devices.get("signal")
        assert device.last_connection_test is not None
    create_snapshot(load_project(source / "scopecat.toml"), tmp_path / "backup")
    restore_snapshot(tmp_path / "backup", tmp_path / "restored")
    with LocalDaemonRuntime(
        tmp_path / "restored", instrument_endpoint=_endpoint()
    ) as restored:
        assert restored.application.devices.get("signal") == device
        assert restored.application.setup.resolve("bench").ref == setup.ref
        assert restored.application.setup.get(setup.id) == setup
