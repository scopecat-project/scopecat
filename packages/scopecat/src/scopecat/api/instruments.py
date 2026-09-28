"""Notebook-facing direct instrument interaction."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Event, Lock, Thread
from time import monotonic
from types import TracebackType
from typing import Protocol, Self
from uuid import uuid4
from weakref import finalize

import httpx2

from scopecat.daemon.client import (
    DaemonClient,
    DaemonUnavailableError,
)
from scopecat.daemon.views import InstrumentListView, InstrumentView
from scopecat.daemon.wire import (
    InstrumentConfiguredDefaultsApplyCommand,
    InstrumentReleaseCommand,
    InstrumentReleaseReceipt,
    InstrumentSessionEndReceipt,
    InstrumentSessionLeaseReceipt,
    InstrumentSessionOpenCommand,
    InstrumentSessionOpenReceipt,
)
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.state import PayloadRef, StateLiteral, StateValue
from scopecat.records.config import InstrumentBindingSpec, InstrumentConnection
from scopecat.records.content import CommandPayload
from scopecat.records.instrument import (
    InstrumentStateCacheReadback,
    InstrumentStateReadback,
    InstrumentStateSnapshot,
    state_member_target,
)
from scopecat.records.setup import SetupRevisionRef
from scopecat.sdk.instruments.commands import (
    ApplyReceipt,
    CollectReceipt,
    InstrumentConfiguredDefaultsApplyReceipt,
    InstrumentOperationArgument,
    InstrumentStateAssignment,
    InstrumentStateCommand,
    InstrumentStateReadCommand,
    InteractiveCollectIntent,
    InvokeCommand,
    InvokeReceipt,
)
from scopecat.sdk.instruments.contracts import InstrumentDescription
from scopecat.sdk.instruments.members import (
    AcquisitionRef,
    AcquisitionResultRef,
    DevicePropertyRef,
    InterfaceRef,
    OperationArgumentRef,
    OperationRef,
    PropertyRef,
    StateMemberRef,
)

type OperationArgumentValue = (
    bool | int | float | str | Quantity | StateValue | CommandPayload
)

_HEARTBEAT_JOIN_SECONDS = 0.2


class InstrumentClientFactory[ClientT](Protocol):
    """Bind one typed notebook client to an already selected instrument."""

    def __call__(
        self,
        session: InstrumentClientChannel,
        instrument_id: str,
        /,
    ) -> ClientT: ...


@dataclass(frozen=True, slots=True)
class InstrumentRef[ClientT]:
    """Project-owned physical instrument identity with a typed client contract."""

    instrument_id: str
    client_factory: InstrumentClientFactory[ClientT] = field(
        repr=False,
        compare=False,
    )
    requires: tuple[InterfaceRef, ...] = ()
    component_path: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.instrument_id:
            raise ValueError("typed instrument id must be non-empty")
        if any(not component_id for component_id in self.component_path):
            raise ValueError("typed instrument component ids must be non-empty")
        interface_ids = tuple(item.interface_id for item in self.requires)
        if len(interface_ids) != len(set(interface_ids)):
            raise ValueError("typed instrument requirements must be unique")


def instrument[ClientT](
    instrument_id: str,
    client_factory: InstrumentClientFactory[ClientT],
    *,
    requires: Sequence[InterfaceRef] = (),
    component_path: Sequence[str] = (),
) -> InstrumentRef[ClientT]:
    """Declare one typed physical instrument reference for notebook use."""

    return InstrumentRef(
        instrument_id=instrument_id,
        client_factory=client_factory,
        requires=tuple(requires),
        component_path=tuple(component_path),
    )


@dataclass(frozen=True, slots=True)
class TemporaryInstrumentRef[ClientT]:
    """Session-only instrument identity with no config or entity routing entry."""

    binding: InstrumentBindingSpec
    client_factory: InstrumentClientFactory[ClientT] = field(
        repr=False,
        compare=False,
    )
    requires: tuple[InterfaceRef, ...] = ()
    component_path: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        interface_ids = tuple(item.interface_id for item in self.requires)
        if len(interface_ids) != len(set(interface_ids)):
            raise ValueError("typed instrument requirements must be unique")
        if any(not component_id for component_id in self.component_path):
            raise ValueError("typed instrument component ids must be non-empty")

    @property
    def instrument_id(self) -> str:
        return self.binding.id


def temporary_instrument[ClientT](
    reference: InstrumentRef[ClientT],
    *,
    driver_id: str,
    connection: InstrumentConnection,
) -> TemporaryInstrumentRef[ClientT]:
    """Declare one instrument attached only for the lifetime of a direct session."""

    return TemporaryInstrumentRef(
        binding=InstrumentBindingSpec(
            id=reference.instrument_id,
            driver_id=driver_id,
            connection=connection,
        ),
        client_factory=reference.client_factory,
        requires=reference.requires,
        component_path=reference.component_path,
    )


class InstrumentIdentity(Protocol):
    """Non-generic identity view accepted by heterogeneous session selection."""

    @property
    def instrument_id(self) -> str: ...


type InstrumentSelection = InstrumentIdentity


class _InstrumentSessionHeartbeat:
    def __init__(
        self,
        client: DaemonClient,
        session: InstrumentSessionOpenReceipt,
    ) -> None:
        self._client = client
        self._session_id = session.session_id
        self._initial_timing = _instrument_lease_timing(session)
        self._stop = Event()
        self._lock = Lock()
        self._failure: Exception | None = None
        self._thread = Thread(
            target=self._run,
            name="scopecat-instrument-session-heartbeat",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def require_live(self) -> None:
        with self._lock:
            failure = self._failure
        if failure is not None:
            raise RuntimeError("instrument session lease renewal failed") from failure

    def request_stop(self) -> None:
        self._stop.set()

    def close(self) -> None:
        self.request_stop()
        self._thread.join(timeout=_HEARTBEAT_JOIN_SECONDS)

    def _run(self) -> None:
        deadline, delay, retry_delay = self._initial_timing
        while not self._stop.wait(delay):
            try:
                lease = self._client.renew_instrument_session(self._session_id)
            except Exception as error:
                if isinstance(error, (DaemonUnavailableError, httpx2.TransportError)):
                    remaining = deadline - monotonic()
                    if remaining > 0:
                        delay = min(0.25, retry_delay, remaining)
                        continue
                with self._lock:
                    self._failure = error
                return
            deadline, delay, retry_delay = _instrument_lease_timing(lease)


def _instrument_lease_timing(
    lease: InstrumentSessionOpenReceipt | InstrumentSessionLeaseReceipt,
) -> tuple[float, float, float]:
    now = datetime.now(UTC)
    ttl = lease.expires_at - lease.renewed_at
    remaining = max((lease.expires_at - now).total_seconds(), 0.0)
    renewal_at = lease.renewed_at + ttl / 3
    delay = max((renewal_at - now).total_seconds(), 0.0)
    return monotonic() + remaining, delay, ttl.total_seconds() / 6


class LabInstrumentOperations:
    """Discover configured instruments and create daemon-owned live sessions."""

    def __init__(
        self,
        client: DaemonClient,
        *,
        operator: str,
    ) -> None:
        self._client = client
        self._operator = operator

    def list(self, *, setup: SetupRevisionRef) -> InstrumentListView:
        return self._client.list_instruments(setup=setup)

    def release(
        self,
        instrument: str | InstrumentIdentity,
        *additional_instruments: str | InstrumentIdentity,
        setup: SetupRevisionRef,
    ) -> InstrumentReleaseReceipt:
        """Disconnect idle devices for another controller to use.

        Runs and sessions must have ended first. The next open or run reconnects
        automatically. This releases connections, not a reservation for the other
        controller: do not submit new work until that controller has finished.
        """
        return self._client.release_instruments(
            InstrumentReleaseCommand(
                setup=setup,
                instrument_ids=tuple(
                    item if isinstance(item, str) else item.instrument_id
                    for item in (instrument, *additional_instruments)
                ),
            )
        )

    def get(self, instrument_id: str, *, setup: SetupRevisionRef) -> InstrumentView:
        return self._client.get_instrument(instrument_id, setup=setup)

    def open(
        self,
        instrument: InstrumentSelection,
        *additional_instruments: InstrumentSelection,
        setup: SetupRevisionRef,
    ) -> InstrumentSessionHandle:
        instrument_ids = tuple(
            _selected_instrument_id(item)
            for item in (instrument, *additional_instruments)
        )
        temporary_bindings = tuple(
            item.binding
            for item in (instrument, *additional_instruments)
            if isinstance(item, TemporaryInstrumentRef)
        )
        return InstrumentSessionHandle(
            setup=setup,
            client=self._client,
            instrument_ids=instrument_ids,
            actor=self._operator,
            temporary_bindings=temporary_bindings,
        )


class InstrumentSessionHandle:
    """Context-managed direct ownership of one or more instruments."""

    def __init__(
        self,
        *,
        client: DaemonClient,
        setup: SetupRevisionRef,
        instrument_ids: tuple[str, ...],
        actor: str,
        temporary_bindings: tuple[InstrumentBindingSpec, ...] = (),
    ) -> None:
        if not instrument_ids or any(not item for item in instrument_ids):
            raise ValueError("direct interaction requires non-empty instrument ids")
        if len(instrument_ids) != len(set(instrument_ids)):
            raise ValueError("direct interaction instrument ids must be unique")
        if not actor:
            raise ValueError("direct interaction actor must be non-empty")
        self._client = client
        self._instrument_ids = instrument_ids
        self._setup = setup
        self._actor = actor
        self._temporary_bindings = temporary_bindings
        self._open_operation_id = _new_command_id("open", instrument_ids[0])
        self._session: InstrumentSessionOpenReceipt | None = None
        self._heartbeat: _InstrumentSessionHeartbeat | None = None
        self._heartbeat_finalizer: finalize[[], Self] | None = None
        self._ended = False

    def __enter__(self) -> Self:
        self._ensure_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_value, traceback
        if exc_type is None:
            self.close()
        else:
            self.abort()

    @property
    def instrument_ids(self) -> tuple[str, ...]:
        return self._instrument_ids

    def __getitem__[ClientT](
        self,
        target: InstrumentRef[ClientT] | TemporaryInstrumentRef[ClientT],
    ) -> ClientT:
        """Bind the target's statically typed client inside this ownership epoch."""

        selected = self._selected_instrument_id(target.instrument_id)
        if target.requires:
            description = self._describe(selected)
            missing = tuple(
                requirement.interface_id
                for requirement in target.requires
                if not _implements_interface_at(
                    description,
                    requirement.interface_id,
                    target.component_path,
                )
            )
            if missing:
                raise ValueError(
                    f"instrument {selected!r} does not implement required interfaces: "
                    + ", ".join(missing)
                )
        return target.client_factory(
            InstrumentClientChannel(self, target.component_path),
            selected,
        )

    def _describe(
        self,
        instrument_id: str | None = None,
    ) -> InstrumentDescription:
        selected = self._selected_instrument_id(instrument_id)
        session = self._require_session()
        return next(
            description
            for description in session.descriptions
            if description.instrument_id == selected
        )

    def _observed_state(
        self,
        instrument_id: str | None = None,
    ) -> InstrumentStateSnapshot:
        """Return the state observed when this session opened."""

        selected = self._selected_instrument_id(instrument_id)
        session = self._require_session()
        return next(
            state.model_copy(deep=True)
            for state in session.observed_state
            if state.instrument_id == selected
        )

    def _read_state(
        self,
        instrument_id: str | None = None,
    ) -> InstrumentStateSnapshot:
        selected = self._selected_instrument_id(instrument_id)
        session = self._require_session()
        return self._client.read_instrument_state(
            session.session_id,
            selected,
        )

    def _read_state_members(
        self,
        targets: Sequence[StateMemberRef],
        /,
        *,
        instrument_id: str | None = None,
    ) -> InstrumentStateReadback:
        selected = self._selected_instrument_id(instrument_id)
        session = self._require_session()
        return self._client.read_instrument_state_members(
            session.session_id,
            selected,
            InstrumentStateReadCommand(
                targets=[state_member_target(target) for target in targets]
            ),
        )

    def _observed_state_members(
        self,
        targets: Sequence[StateMemberRef],
        /,
        *,
        instrument_id: str | None = None,
    ) -> InstrumentStateCacheReadback:
        selected = self._selected_instrument_id(instrument_id)
        session = self._require_session()
        return self._client.read_observed_instrument_state_members(
            session.session_id,
            selected,
            InstrumentStateReadCommand(
                targets=[state_member_target(target) for target in targets]
            ),
        )

    def _apply_configured_defaults(
        self,
        *,
        instrument_id: str | None = None,
    ) -> InstrumentConfiguredDefaultsApplyReceipt:
        """Apply sparse defaults pinned at session open without resetting hardware."""

        selected = self._selected_instrument_id(instrument_id)
        session = self._require_session()
        return self._client.apply_instrument_configured_defaults(
            session.session_id,
            selected,
            InstrumentConfiguredDefaultsApplyCommand(
                operation_id=_new_command_id("configured_defaults", selected)
            ),
        )

    def _apply(
        self,
        values: Mapping[PropertyRef, StateLiteral | StateValue],
        /,
        *,
        instrument_id: str | None = None,
    ) -> ApplyReceipt:
        if not values:
            raise ValueError("interactive apply requires at least one property")
        selected = self._selected_instrument_id(instrument_id)
        session = self._require_session()
        command = InstrumentStateCommand(
            command_id=_new_command_id("apply", selected),
            instrument_id=selected,
            assignments=[
                InstrumentStateAssignment(
                    resource_id=selected,
                    target=state_member_target(target),
                    value=(
                        value if isinstance(value, StateValue) else StateValue(value)
                    ),
                )
                for target, value in values.items()
            ],
        )
        return self._client.apply_instrument_state(
            session.session_id,
            selected,
            command,
        )

    def _invoke(
        self,
        operation: OperationRef,
        arguments: Mapping[OperationArgumentRef, OperationArgumentValue] | None = None,
        /,
        *,
        instrument_id: str | None = None,
    ) -> InvokeReceipt:
        selected_arguments = dict(arguments or {})
        if any(target.operation != operation for target in selected_arguments):
            raise ValueError(
                "operation arguments must belong to the selected operation"
            )
        selected = self._selected_instrument_id(instrument_id)
        session = self._require_session()
        payloads: dict[str, CommandPayload] = {}
        for value in selected_arguments.values():
            if not isinstance(value, CommandPayload):
                continue
            previous = payloads.setdefault(value.id, value)
            if previous != value:
                raise ValueError(
                    f"operation payload id {value.id!r} has different content"
                )
        command = InvokeCommand(
            command_id=_new_command_id("invoke", selected),
            instrument_id=selected,
            resource_id=selected,
            interface_id=operation.interface_id,
            component_path=list(operation.component_path),
            operation_id=operation.operation_id,
            arguments=[
                InstrumentOperationArgument(
                    id=target.argument_id,
                    value=(
                        value
                        if isinstance(value, StateValue)
                        else (
                            StateValue(PayloadRef(payload_id=value.id))
                            if isinstance(value, CommandPayload)
                            else StateValue(value)
                        )
                    ),
                )
                for target, value in selected_arguments.items()
            ],
            payloads=payloads,
        )
        return self._client.invoke_instrument(
            session.session_id,
            selected,
            command,
        )

    def _collect(
        self,
        acquisition: AcquisitionRef,
        *results: AcquisitionResultRef,
        instrument_id: str | None = None,
    ) -> CollectReceipt:
        selected = self._selected_instrument_id(instrument_id)
        if any(result.acquisition != acquisition for result in results):
            raise ValueError("collect results must belong to the selected acquisition")

        session = self._require_session()
        intent = InteractiveCollectIntent(
            command_id=_new_command_id("collect", selected),
            instrument_id=selected,
            interface_id=acquisition.interface_id,
            component_path=list(acquisition.component_path),
            acquisition_id=acquisition.acquisition_id,
            result_ids=[result.result_id for result in results],
        )
        return self._client.collect_instrument(
            session.session_id,
            selected,
            intent,
        )

    def close(
        self,
    ) -> InstrumentSessionEndReceipt | None:
        return self._end(abort=False)

    def abort(
        self,
    ) -> InstrumentSessionEndReceipt | None:
        return self._end(abort=True)

    def _end(
        self,
        *,
        abort: bool,
    ) -> InstrumentSessionEndReceipt | None:
        if self._ended:
            return None
        session = self._session
        if session is None:
            self._ended = True
            return None
        receipt = (
            self._client.abort_instrument_session(session.session_id)
            if abort
            else self._client.close_instrument_session(session.session_id)
        )
        self._ended = True
        self._stop_heartbeat()
        return receipt

    def _ensure_open(self) -> InstrumentSessionOpenReceipt:
        if self._ended:
            raise RuntimeError("instrument session is already closed")
        session = self._session
        if session is None:
            session = self._client.open_instrument_session(
                InstrumentSessionOpenCommand(
                    setup=self._setup,
                    operation_id=self._open_operation_id,
                    actor=self._actor,
                    instrument_ids=self._instrument_ids,
                    temporary_bindings=self._temporary_bindings,
                )
            )
            heartbeat = _InstrumentSessionHeartbeat(self._client, session)
            self._session = session
            self._heartbeat = heartbeat
            self._heartbeat_finalizer = finalize(self, heartbeat.request_stop)
            heartbeat.start()
        else:
            heartbeat = self._heartbeat
            assert heartbeat is not None
            heartbeat.require_live()
        return session

    def _stop_heartbeat(self) -> None:
        heartbeat = self._heartbeat
        if heartbeat is None:
            return
        heartbeat.close()
        finalizer = self._heartbeat_finalizer
        if finalizer is not None:
            finalizer.detach()
        self._heartbeat = None
        self._heartbeat_finalizer = None

    def _require_session(self) -> InstrumentSessionOpenReceipt:
        return self._ensure_open()

    def _selected_instrument_id(self, instrument_id: str | None) -> str:
        if instrument_id is None:
            if len(self._instrument_ids) != 1:
                raise ValueError("multi-instrument sessions require an instrument_id")
            return self._instrument_ids[0]
        if instrument_id not in self._instrument_ids:
            raise ValueError(f"instrument {instrument_id!r} is not in this session")
        return instrument_id


@dataclass(frozen=True, slots=True)
class InstrumentClientChannel:
    """Low-level channel used only to implement typed instrument clients."""

    _session: InstrumentSessionHandle = field(repr=False)
    _component_path: tuple[str, ...] = ()

    @property
    def raw_session(self) -> InstrumentSessionHandle:
        """Return the backing session for SDK integrations and tests."""

        return self._session

    @property
    def component_path(self) -> tuple[str, ...]:
        """Return the physical component prefix selected by the typed reference."""

        return self._component_path

    def state_member_ref(self, target: StateMemberRef, /) -> StateMemberRef:
        """Map a client-relative state member onto the selected component."""

        if isinstance(target, PropertyRef):
            return PropertyRef(
                target.interface_id,
                (*self._component_path, *target.component_path),
                target.property_id,
            )
        return DevicePropertyRef(
            target.schema_id,
            (*self._component_path, *target.component_path),
            target.property_id,
        )

    def describe(self, instrument_id: str) -> InstrumentDescription:
        return self._session._describe(  # pyright: ignore[reportPrivateUsage]
            instrument_id
        )

    def observed_state(self, instrument_id: str) -> InstrumentStateSnapshot:
        return self._session._observed_state(  # pyright: ignore[reportPrivateUsage]
            instrument_id
        )

    def read_state(self, instrument_id: str) -> InstrumentStateSnapshot:
        return self._session._read_state(  # pyright: ignore[reportPrivateUsage]
            instrument_id
        )

    def read_state_members(
        self,
        instrument_id: str,
        *targets: StateMemberRef,
    ) -> InstrumentStateReadback:
        return self._session._read_state_members(  # pyright: ignore[reportPrivateUsage]
            tuple(self.state_member_ref(target) for target in targets),
            instrument_id=instrument_id,
        )

    def observed_state_members(
        self,
        instrument_id: str,
        *targets: StateMemberRef,
    ) -> InstrumentStateCacheReadback:
        return self._session._observed_state_members(  # pyright: ignore[reportPrivateUsage]
            tuple(self.state_member_ref(target) for target in targets),
            instrument_id=instrument_id,
        )

    def apply_configured_defaults(
        self,
        instrument_id: str,
    ) -> InstrumentConfiguredDefaultsApplyReceipt:
        if self._component_path:
            raise TypeError(
                "configured defaults belong to the whole physical instrument, "
                "not one mounted component"
            )
        return self._session._apply_configured_defaults(  # pyright: ignore[reportPrivateUsage]
            instrument_id=instrument_id
        )

    def apply(
        self,
        values: Mapping[PropertyRef, StateLiteral | StateValue],
        *,
        instrument_id: str,
    ) -> ApplyReceipt:
        return self._session._apply(  # pyright: ignore[reportPrivateUsage]
            {
                _mount_property_ref(target, self._component_path): value
                for target, value in values.items()
            },
            instrument_id=instrument_id,
        )

    def invoke(
        self,
        operation: OperationRef,
        arguments: Mapping[OperationArgumentRef, OperationArgumentValue] | None = None,
        *,
        instrument_id: str,
    ) -> InvokeReceipt:
        return self._session._invoke(  # pyright: ignore[reportPrivateUsage]
            _mount_operation_ref(operation, self._component_path),
            (
                None
                if arguments is None
                else {
                    _mount_operation_argument_ref(
                        target,
                        self._component_path,
                    ): value
                    for target, value in arguments.items()
                }
            ),
            instrument_id=instrument_id,
        )

    def collect(
        self,
        acquisition: AcquisitionRef,
        *results: AcquisitionResultRef,
        instrument_id: str,
    ) -> CollectReceipt:
        return self._session._collect(  # pyright: ignore[reportPrivateUsage]
            _mount_acquisition_ref(acquisition, self._component_path),
            *(
                _mount_acquisition_result_ref(result, self._component_path)
                for result in results
            ),
            instrument_id=instrument_id,
        )


def _implements_interface_at(
    description: InstrumentDescription,
    interface_id: str,
    component_path: tuple[str, ...],
) -> bool:
    if interface_id not in {interface.id for interface in description.interfaces}:
        return False
    mounts = tuple(
        tuple(mount.component_path)
        for mount in description.interface_mounts
        if mount.interface_id == interface_id
    )
    if component_path:
        return component_path in mounts
    return not mounts


def _mount_property_ref(
    target: PropertyRef,
    component_path: tuple[str, ...],
) -> PropertyRef:
    return PropertyRef(
        target.interface_id,
        (*component_path, *target.component_path),
        target.property_id,
    )


def _mount_operation_ref(
    target: OperationRef,
    component_path: tuple[str, ...],
) -> OperationRef:
    return OperationRef(
        target.interface_id,
        (*component_path, *target.component_path),
        target.operation_id,
    )


def _mount_operation_argument_ref(
    target: OperationArgumentRef,
    component_path: tuple[str, ...],
) -> OperationArgumentRef:
    return OperationArgumentRef(
        target.interface_id,
        (*component_path, *target.component_path),
        target.operation_id,
        target.argument_id,
    )


def _mount_acquisition_ref(
    target: AcquisitionRef,
    component_path: tuple[str, ...],
) -> AcquisitionRef:
    return AcquisitionRef(
        target.interface_id,
        (*component_path, *target.component_path),
        target.acquisition_id,
    )


def _mount_acquisition_result_ref(
    target: AcquisitionResultRef,
    component_path: tuple[str, ...],
) -> AcquisitionResultRef:
    return AcquisitionResultRef(
        target.interface_id,
        (*component_path, *target.component_path),
        target.acquisition_id,
        target.result_id,
    )


def _new_command_id(kind: str, subject: str) -> str:
    return f"interactive.{kind}.{subject}.{uuid4().hex}"


def _selected_instrument_id(selection: InstrumentSelection) -> str:
    return selection.instrument_id


__all__ = [
    "InstrumentClientChannel",
    "InstrumentClientFactory",
    "InstrumentIdentity",
    "InstrumentRef",
    "InstrumentSelection",
    "InstrumentSessionHandle",
    "LabInstrumentOperations",
    "OperationArgumentValue",
    "TemporaryInstrumentRef",
    "instrument",
    "temporary_instrument",
]
