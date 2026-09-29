"""Device maintenance shares admission fences and resident connection ownership."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Generator
from contextlib import ExitStack, contextmanager
from datetime import timedelta
from threading import Lock
from typing import TYPE_CHECKING, Protocol, cast
from uuid import uuid4

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from scopecat.control.models import ResourceKey
from scopecat.daemon.device_views import DeviceConnectionTest, DeviceView
from scopecat.daemon.wire import (
    DeviceProbeCommand,
    DeviceSaveCommand,
    InstrumentDriverProbeCommand,
    InstrumentDriverProbeReceipt,
)
from scopecat.records.config import InstrumentBindingSpec, RoutingGraph, Topology
from scopecat.records.device import (
    DeviceConnection,
    DeviceConnectionRevision,
    DeviceRevisionRef,
    DriverImplementationRef,
    RegisteredDevice,
    device_resource_key,
)
from scopecat.records.setup import (
    SetupDefinition,
    SetupDefinitionRevision,
    SetupInstrumentBinding,
    SetupRevision,
)

from scopecat_server.errors import BackendConflict, BackendNotFound
from scopecat_server.instruments.actors import (
    InstrumentActorConflict,
    InstrumentActorRegistry,
    InstrumentActorShutdown,
)
from scopecat_server.instruments.backend import InstrumentBackendEndpoint
from scopecat_server.storage.sqlite.control_plane import (
    ControlPlaneConflict,
    SQLiteControlPlane,
)
from scopecat_server.storage.sqlite.devices import DeviceRepository
from scopecat_server.storage.sqlite.setups import SQLiteSetupRepository

if TYPE_CHECKING:
    from scopecat_server.instruments.runtime import InstrumentRuntime


class _OptionsValidator(Protocol):
    def validate(self, instance: object) -> None: ...


class DeviceService:
    def __init__(
        self,
        *,
        control: SQLiteControlPlane,
        actors: InstrumentActorRegistry,
        endpoint: InstrumentBackendEndpoint | None,
    ) -> None:
        self.control = control
        self.actors = actors
        self.endpoint = endpoint
        self._mutation_lock = Lock()

    def driver_ref(self, driver_id: str) -> DriverImplementationRef:
        endpoint = self.endpoint
        if endpoint is None:
            raise BackendConflict(
                "no installed instrument driver capability is available"
            )
        if endpoint.driver_catalog.get(driver_id) is None:
            raise BackendNotFound(f"installed driver does not exist: {driver_id}")
        return DriverImplementationRef(
            provider_id=endpoint.provider_id,
            driver_id=driver_id,
            artifact_hash=endpoint.artifact_hash,
        )

    def require_driver(self, reference: DriverImplementationRef) -> None:
        if self.driver_ref(reference.driver_id) != reference:
            raise BackendConflict(
                "installed driver changed; review the device connection again"
            )

    def validate_connection(self, content: DeviceConnection) -> None:
        self.require_driver(content.driver)
        assert self.endpoint is not None
        self._validate_options(content, self.endpoint)

    @staticmethod
    def _validate_options(
        content: DeviceConnection, endpoint: InstrumentBackendEndpoint
    ) -> None:
        driver = endpoint.driver_catalog.get(content.driver.driver_id)
        if driver is None:
            raise BackendConflict(
                f"replacement does not provide driver {content.driver.driver_id}"
            )
        contract = next(
            (
                item
                for item in driver.connections
                if item.kind == content.connection.kind
            ),
            None,
        )
        if contract is None:
            raise BackendConflict(
                f"{driver.driver_id} does not support "
                f"{content.connection.kind} connections"
            )
        try:
            validator = cast(
                "_OptionsValidator", Draft202012Validator(contract.options_schema)
            )
            validator.validate(content.connection.options)
        except ValidationError as error:
            raise BackendConflict(
                f"invalid connection options: {error.message}"
            ) from error

    def list(self) -> tuple[DeviceView, ...]:
        with self.control.read_transaction() as connection:
            claims = {
                claim.resource.id: claim
                for claim in self.control.list_resource_claims_in_transaction(
                    connection
                )
                if claim.resource.kind == "instrument"
            }
            return tuple(
                view.model_copy(
                    update={
                        "availability": claim.status,
                        "owner_kind": claim.owner_kind,
                        "owner_id": claim.owner_id,
                    }
                )
                if (claim := claims.get(device_resource_key(view.device.id)))
                is not None
                else view
                for view in DeviceRepository(connection).list()
            )

    def replace_backend(
        self,
        replacement: InstrumentBackendEndpoint,
        instruments: InstrumentRuntime,
        *,
        actor: str,
        publish_source: Callable[[sqlite3.Connection], None] | None = None,
    ) -> tuple[DeviceView, ...]:
        """Install an already validated worker and retain new device revisions.

        Ownership of the replacement transfers here, including cleanup on failure.
        The source coordinator publishes its selection in the same transaction
        as device revisions via publish_source.
        """
        published = False
        try:
            with self._errors(), self._mutation_lock:
                previous = self.endpoint
                if replacement is previous:
                    raise BackendConflict("replacement requires a new backend")
                if not replacement.healthy:
                    raise BackendConflict("replacement backend is unavailable")
                if (
                    previous is not None
                    and replacement.provider_id != previous.provider_id
                ):
                    raise BackendConflict("replacement must keep the provider identity")
                devices = tuple(
                    view for view in self.list() if view.device.state != "retired"
                )
                revisions = tuple(
                    DeviceConnectionRevision(
                        id=f"driver-update:{uuid4().hex}",
                        device_id=view.device.id,
                        previous=view.device.head,
                        content=view.revision.content.model_copy(
                            update={
                                "driver": DriverImplementationRef(
                                    provider_id=replacement.provider_id,
                                    driver_id=view.revision.content.driver.driver_id,
                                    artifact_hash=replacement.artifact_hash,
                                )
                            }
                        ),
                        actor=actor,
                        note="Driver source updated",
                    )
                    for view in devices
                )
                for revision in revisions:
                    self._validate_options(revision.content, replacement)
                with ExitStack() as stack, instruments.replace_backend(replacement):
                    keys = tuple(
                        dict.fromkeys(
                            (
                                *self.actors.connection_keys(),
                                *(
                                    device_resource_key(view.device.id)
                                    for view in devices
                                ),
                            )
                        )
                    )
                    retirement = (
                        stack.enter_context(self.actors.begin_retirement(keys))
                        if keys
                        else None
                    )
                    # Reserve every device before disconnecting any of them. Runs
                    # admitted concurrently either own a claim or see maintenance.
                    sessions: dict[str, str] = {}
                    for view in devices:
                        device_id = view.device.id
                        with self.control.read_transaction() as connection:
                            self._require_drained(connection, device_id)
                        snapshot = self.access_setup(device_id, require_driver=False)
                        session = self.control.open_instrument_session(
                            operation_id=f"driver-update:{uuid4().hex}",
                            actor=actor,
                            setup=snapshot.ref,
                            instrument_ids=(device_id,),
                            exclusivity_keys=(device_resource_key(device_id),),
                            ttl=timedelta(minutes=5),
                        )
                        sessions[device_id] = session.session_id
                        stack.callback(self._close_maintenance, session.session_id)
                    try:
                        if retirement is not None:
                            retirement.retire_idle()
                    except Exception as error:
                        for session_id in sessions.values():
                            self.control.mark_instrument_session_unknown(
                                session_id, reason="driver_update_retirement_failed"
                            )
                        raise BackendConflict(
                            "driver update could not release existing connections; "
                            "inspect device attention before retrying"
                        ) from error
                    with self.control.write_transaction() as connection:
                        repository = DeviceRepository(connection)
                        for view, revision in zip(devices, revisions, strict=True):
                            self._require_drained(
                                connection, view.device.id, sessions[view.device.id]
                            )
                            repository.save(
                                label=view.device.label,
                                revision=revision,
                                expected_head=view.device.head,
                            )
                        if publish_source is not None:
                            publish_source(connection)
                    self.endpoint = replacement
                    published = True
                return self.list()
        finally:
            if not published and replacement is not self.endpoint:
                replacement.shutdown()

    def _close_maintenance(self, session_id: str) -> None:
        if self.control.get_instrument_session(session_id).state == "active":
            self.control.close_instrument_session(session_id, status="closed")

    def get(self, device_id: str) -> DeviceView:
        with self._errors(), self.control.read_transaction() as connection:
            return DeviceRepository(connection).view(device_id)

    def access_setup(
        self, device_id: str, *, require_driver: bool = True
    ) -> SetupRevision:
        """Retain an operation snapshot without requiring a user-authored setup."""
        with self._errors(), self.control.write_transaction() as connection:
            view = DeviceRepository(connection).view(device_id)
            if require_driver:
                self.require_driver(view.revision.content.driver)
            safety = view.revision.content.safety
            definition = SetupDefinitionRevision(
                id=f"device-access:{device_id}:{view.device.head.revision_id}",
                purpose="device_access",
                definition=SetupDefinition(
                    topology=Topology(),
                    instruments=(
                        SetupInstrumentBinding(
                            id=device_id,
                            device_id=device_id,
                            run_start="preserve",
                            success_action="apply_safe_state"
                            if safety.require_safe_success
                            else "release",
                            failure_action="abort_then_safe_state"
                            if safety.require_safe_failure
                            else "abort_and_release",
                        ),
                    ),
                    routing=RoutingGraph(),
                    domain_target=None,
                ),
                actor="scopecat",
            )
            setups = SQLiteSetupRepository(connection)
            setups.save_definition(definition)
            return setups.resolve(definition.id)

    def save(self, command: DeviceSaveCommand) -> DeviceView:
        with self._errors(), self._mutation_lock:
            self.validate_connection(command.connection)
            revision = DeviceConnectionRevision(
                id=command.revision_id,
                device_id=command.device_id,
                content=command.connection,
                previous=command.expected_head,
                actor=command.actor,
                note=command.note,
            )
            with self.control.read_transaction() as connection:
                repository = DeviceRepository(connection)
                repository.validate_save(
                    label=command.label,
                    revision=revision,
                    expected_head=command.expected_head,
                )
                try:
                    current = repository.view(command.device_id)
                except KeyError:
                    current = None
                if current is not None and current.device.head == revision.ref:
                    if current.revision.model_dump(
                        exclude={"recorded_at"}
                    ) != revision.model_dump(exclude={"recorded_at"}):
                        raise BackendConflict(
                            "device revision ID already has different provenance"
                        )
                    return current
            with (
                self._maintenance(
                    command.device_id, command.expected_head
                ) as maintenance,
                self.control.write_transaction() as connection,
            ):
                self._require_drained(connection, command.device_id, maintenance)
                result = DeviceRepository(connection).save(
                    label=command.label,
                    revision=revision,
                    expected_head=command.expected_head,
                )
                if maintenance is not None:
                    self.control.close_instrument_session_in_transaction(
                        connection, maintenance, status="closed"
                    )
                return result

    def test_connection(
        self,
        device_id: str,
        command: DeviceProbeCommand,
        instruments: InstrumentRuntime,
    ) -> InstrumentDriverProbeReceipt:
        with self.control.read_transaction() as connection:
            prior = DeviceRepository(connection).connection_test(command.operation_id)
        if prior is not None:
            if (
                prior.revision != command.expected_head
                or prior.actor != command.actor
                or prior.revision.device_id != device_id
            ):
                raise BackendConflict("connection test operation has different intent")
            if prior.error is not None:
                raise BackendConflict(prior.error)
            return InstrumentDriverProbeReceipt(
                status="connected", description=prior.description
            )
        revision = self.access_setup(device_id)
        if revision.resolution.devices != (command.expected_head,):
            raise BackendConflict("device connection changed; review it before testing")
        spec = revision.setup.instrument_registry.instruments[0]
        try:
            result = instruments.probe_driver(
                InstrumentDriverProbeCommand(
                    setup=revision.ref,
                    operation_id=command.operation_id,
                    actor=command.actor,
                    binding=InstrumentBindingSpec(
                        id=spec.id, driver_id=spec.driver_id, connection=spec.connection
                    ),
                )
            )
        except BackendConflict as error:
            evidence = DeviceConnectionTest(
                operation_id=command.operation_id,
                revision=command.expected_head,
                actor=command.actor,
                error=str(error),
            )
        else:
            evidence = DeviceConnectionTest(
                operation_id=command.operation_id,
                revision=command.expected_head,
                actor=command.actor,
                description=result.description,
            )
        with self._errors(), self.control.write_transaction() as connection:
            recorded = DeviceRepository(connection).save_connection_test(evidence)
        if recorded.error is not None:
            raise BackendConflict(recorded.error)
        return InstrumentDriverProbeReceipt(
            status="connected", description=recorded.description
        )

    def rename(self, device_id: str, label: str) -> RegisteredDevice:
        with (
            self._errors(),
            self._mutation_lock,
            self.control.write_transaction() as connection,
        ):
            return DeviceRepository(connection).rename(device_id, label)

    def retire(
        self, device_id: str, expected_head: DeviceRevisionRef
    ) -> RegisteredDevice:
        current = self.get(device_id).device
        if current.state == "retired" and current.head == expected_head:
            return current
        with (
            self._errors(),
            self._mutation_lock,
            self._maintenance(device_id, expected_head) as maintenance,
            self.control.write_transaction() as connection,
        ):
            self._require_drained(connection, device_id, maintenance)
            result = DeviceRepository(connection).retire(device_id, expected_head)
            assert maintenance is not None
            self.control.close_instrument_session_in_transaction(
                connection, maintenance, status="closed"
            )
            return result

    @contextmanager
    def _maintenance(
        self, device_id: str, expected_head: DeviceRevisionRef | None
    ) -> Generator[str | None]:
        with self.control.read_transaction() as connection:
            repository = DeviceRepository(connection)
            try:
                current = repository.get(device_id)
            except KeyError:
                current = None
            if (current.head if current is not None else None) != expected_head:
                raise BackendConflict(
                    "device connection changed; reload before maintenance"
                )
            self._require_drained(connection, device_id)
        with self.actors.begin_retirement(
            (device_resource_key(device_id),)
        ) as retirement:
            if current is None:
                yield None
                return
            snapshot = self.access_setup(device_id, require_driver=False)
            session = self.control.open_instrument_session(
                operation_id=f"device-maintenance:{uuid4().hex}",
                actor="device maintenance",
                setup=snapshot.ref,
                instrument_ids=(device_id,),
                exclusivity_keys=(device_resource_key(device_id),),
                ttl=timedelta(minutes=5),
            )
            try:
                retirement.retire_idle()
            except Exception as error:
                self.control.mark_instrument_session_unknown(
                    session.session_id, reason="device_connection_retirement_failed"
                )
                raise BackendConflict(
                    "device connection could not be retired; restart the application, "
                    f"inspect the device, then resolve session {session.session_id}"
                ) from error
            try:
                yield session.session_id
            finally:
                if (
                    self.control.get_instrument_session(session.session_id).state
                    == "active"
                ):
                    self.control.close_instrument_session(
                        session.session_id, status="closed"
                    )

    def _require_drained(
        self,
        connection: sqlite3.Connection,
        device_id: str,
        maintenance: str | None = None,
    ) -> None:
        if maintenance is not None:
            self.control.validate_instrument_session_in_transaction(
                connection, maintenance
            )
        blockers = self.control.inventory_migration_blockers_in_transaction(
            connection, (ResourceKey.instrument(device_resource_key(device_id)),)
        )
        blockers = tuple(
            item
            for item in blockers
            if not (
                item.owner_kind == "instrument_session" and item.owner_id == maintenance
            )
        )
        if blockers:
            details = ", ".join(
                f"{item.owner_kind} {item.owner_id} ({item.state})" for item in blockers
            )
            raise BackendConflict(
                f"device is in use by {details}; "
                "finish or cancel that work before maintenance"
            )

    @staticmethod
    @contextmanager
    def _errors() -> Generator[None]:
        try:
            yield
        except KeyError as error:
            raise BackendNotFound(str(error)) from error
        except (
            ValueError,
            ControlPlaneConflict,
            InstrumentActorConflict,
            InstrumentActorShutdown,
        ) as error:
            raise BackendConflict(str(error)) from error
