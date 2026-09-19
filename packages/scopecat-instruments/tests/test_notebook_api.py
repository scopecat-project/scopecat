from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from gc import collect as collect_garbage
from threading import Event
from typing import assert_type, override
from weakref import ref

import pytest
from scopecat.api.instruments import (
    InstrumentClientChannel,
    InstrumentRef,
    InstrumentSessionHandle,
    LabInstrumentOperations,
    instrument,
)
from scopecat.daemon.client import DaemonClient
from scopecat.daemon.wire import (
    InstrumentConfiguredDefaultsApplyCommand,
    InstrumentSessionEndReceipt,
    InstrumentSessionLeaseReceipt,
    InstrumentSessionOpenCommand,
    InstrumentSessionOpenReceipt,
)
from scopecat.kernel.errors import ProviderContractError
from scopecat.records.instrument import (
    InstrumentReadback,
    InstrumentStateSnapshot,
)
from scopecat.records.measurement import MeasurementScalar
from scopecat.records.setup import SetupRevisionRef
from scopecat.sdk.instruments import (
    CollectReceipt,
    InstrumentCollectFailure,
    InstrumentComponentSpec,
    InstrumentConfiguredDefaultsApplyReceipt,
    InstrumentDescription,
    InterfaceRef,
    instrument_component,
    interface,
    interface_mount,
)
from scopecat.sdk.instruments.commands import InteractiveCollectIntent
from scopecat.sdk.problems import ProblemPhase, problem

from scopecat_instruments import (
    TemperatureReadback,
    network_sweep,
    temperature_readout,
)
from scopecat_instruments.interfaces import (
    network_sweep_interface,
    temperature_readout_interface,
)
from scopecat_instruments.members import (
    DC_MONITOR_CURRENT_RESULT,
    DC_MONITOR_MEASURE_CURRENT,
    NETWORK_SWEEP_ACQUISITION,
    NETWORK_SWEEP_FREQUENCY_RESULT,
    NETWORK_SWEEP_S_PARAMETER_RESULT,
    TEMPERATURE_READOUT_RESISTANCE_RESULT,
    TEMPERATURE_READOUT_SAMPLE,
    TEMPERATURE_READOUT_TEMPERATURE_RESULT,
)

_DEFAULT_LEASE_DURATION = timedelta(seconds=30)
_HEARTBEAT_LEASE_DURATION = timedelta(milliseconds=30)


@dataclass(frozen=True, slots=True)
class _TypedSourceClient:
    session: InstrumentClientChannel
    instrument_id: str


class _CollectingDaemon(DaemonClient):
    def __init__(
        self,
        description: InstrumentDescription,
        state: InstrumentStateSnapshot,
        *,
        lease_duration: timedelta = _DEFAULT_LEASE_DURATION,
        initial_renewed_at: datetime | None = None,
        readback: InstrumentReadback | None = None,
        collect_receipt: CollectReceipt | None = None,
    ) -> None:
        super().__init__("http://unused.test")
        self.description = description
        self.state = state
        self.lease_duration = lease_duration
        self.initial_renewed_at = initial_renewed_at
        self.readback = InstrumentReadback() if readback is None else readback
        self.collect_receipt = collect_receipt
        self.state_reads = 0
        self.collect_intent: InteractiveCollectIntent | None = None

    @override
    def open_instrument_session(
        self,
        command: InstrumentSessionOpenCommand,
    ) -> InstrumentSessionOpenReceipt:
        renewed_at = self.initial_renewed_at or datetime.now(UTC)
        return InstrumentSessionOpenReceipt(
            session_id="session-1",
            actor=command.actor,
            setup=SetupRevisionRef(
                revision_id="setup-1", content_hash="sha256:" + "0" * 64
            ),
            instrument_ids=command.instrument_ids,
            configured_default_instrument_ids=(),
            descriptions=(self.description,),
            observed_state=(self.state,),
            opened_at=renewed_at,
            renewed_at=renewed_at,
            expires_at=renewed_at + self.lease_duration,
        )

    @override
    def read_instrument_state(
        self,
        session_id: str,
        instrument_id: str,
    ) -> InstrumentStateSnapshot:
        assert session_id == "session-1"
        assert instrument_id == self.description.instrument_id
        self.state_reads += 1
        return self.state

    @override
    def collect_instrument(
        self,
        session_id: str,
        instrument_id: str,
        intent: InteractiveCollectIntent,
    ) -> CollectReceipt:
        assert session_id == "session-1"
        assert instrument_id == self.description.instrument_id
        self.collect_intent = intent
        return self.collect_receipt or CollectReceipt(readback=self.readback)

    @override
    def close_instrument_session(
        self,
        session_id: str,
    ) -> InstrumentSessionEndReceipt:
        return InstrumentSessionEndReceipt(session_id=session_id, status="closed")


class _ConfiguredDefaultsDaemon(DaemonClient):
    def __init__(
        self,
        *,
        interface_ids: tuple[str, ...] = (),
        interface_mounts: tuple[tuple[str, tuple[str, ...]], ...] = (),
        components: tuple[InstrumentComponentSpec, ...] = (),
    ) -> None:
        super().__init__("http://unused.test")
        self.interface_ids = interface_ids
        self.interface_mounts = interface_mounts
        self.components = components
        self.open_commands: list[InstrumentSessionOpenCommand] = []
        self.apply_calls: list[
            tuple[str, str, InstrumentConfiguredDefaultsApplyCommand]
        ] = []

    @override
    def open_instrument_session(
        self,
        command: InstrumentSessionOpenCommand,
    ) -> InstrumentSessionOpenReceipt:
        self.open_commands.append(command)
        renewed_at = datetime.now(UTC)
        return InstrumentSessionOpenReceipt(
            session_id="session-1",
            actor=command.actor,
            setup=SetupRevisionRef(
                revision_id="setup-1", content_hash="sha256:" + "0" * 64
            ),
            instrument_ids=command.instrument_ids,
            configured_default_instrument_ids=command.instrument_ids,
            descriptions=tuple(
                InstrumentDescription(
                    instrument_id=instrument_id,
                    implementation_id="tests.instrument",
                    implementation_version="1",
                    components=list(self.components),
                    interfaces=[interface(id) for id in self.interface_ids],
                    interface_mounts=[
                        interface_mount(interface_id, *component_path)
                        for interface_id, component_path in self.interface_mounts
                    ],
                )
                for instrument_id in command.instrument_ids
            ),
            observed_state=tuple(
                InstrumentStateSnapshot(instrument_id=instrument_id)
                for instrument_id in command.instrument_ids
            ),
            opened_at=renewed_at,
            renewed_at=renewed_at,
            expires_at=renewed_at + timedelta(seconds=30),
        )

    @override
    def apply_instrument_configured_defaults(
        self,
        session_id: str,
        instrument_id: str,
        command: InstrumentConfiguredDefaultsApplyCommand,
    ) -> InstrumentConfiguredDefaultsApplyReceipt:
        self.apply_calls.append((session_id, instrument_id, command))
        return InstrumentConfiguredDefaultsApplyReceipt(
            session_id=session_id,
            operation_id=command.operation_id,
            instrument_id=instrument_id,
            setup=SetupRevisionRef(
                revision_id="setup-1", content_hash="sha256:" + "0" * 64
            ),
            status="unchanged",
            state=InstrumentStateSnapshot(instrument_id=instrument_id),
        )

    @override
    def close_instrument_session(
        self,
        session_id: str,
    ) -> InstrumentSessionEndReceipt:
        return InstrumentSessionEndReceipt(session_id=session_id, status="closed")


class _HeartbeatDaemon(_CollectingDaemon):
    def __init__(
        self,
        *,
        renew_error: Exception | None = None,
        close_error: Exception | None = None,
        lease_duration: timedelta = _HEARTBEAT_LEASE_DURATION,
        initial_renewed_at: datetime | None = None,
    ) -> None:
        description = InstrumentDescription(
            instrument_id="source-a",
            implementation_id="tests.source",
            implementation_version="1",
        )
        super().__init__(
            description,
            InstrumentStateSnapshot(instrument_id="source-a"),
            lease_duration=lease_duration,
            initial_renewed_at=initial_renewed_at,
        )
        self.renew_error = renew_error
        self.close_error = close_error
        self.renew_attempted = Event()
        self.renew_calls = 0
        self.close_calls = 0

    @override
    def renew_instrument_session(
        self,
        session_id: str,
    ) -> InstrumentSessionLeaseReceipt:
        assert session_id == "session-1"
        self.renew_calls += 1
        self.renew_attempted.set()
        if self.renew_error is not None:
            raise self.renew_error
        renewed_at = datetime.now(UTC)
        return InstrumentSessionLeaseReceipt(
            session_id=session_id,
            renewed_at=renewed_at,
            expires_at=renewed_at + self.lease_duration,
        )

    @override
    def close_instrument_session(
        self,
        session_id: str,
    ) -> InstrumentSessionEndReceipt:
        assert session_id == "session-1"
        self.close_calls += 1
        if self.close_error is not None:
            error = self.close_error
            self.close_error = None
            raise error
        return InstrumentSessionEndReceipt(session_id=session_id, status="closed")


def test_session_handle_renews_lease_in_background() -> None:
    daemon = _HeartbeatDaemon()
    handle = InstrumentSessionHandle(
        client=daemon,
        instrument_ids=("source-a",),
        actor="test",
    )

    try:
        handle._observed_state()

        assert daemon.renew_attempted.wait(timeout=5)
        assert daemon.renew_calls >= 1
    finally:
        handle.close()
        daemon.close()


def test_typed_instrument_ref_binds_a_statically_known_client() -> None:
    daemon = _ConfiguredDefaultsDaemon()
    source = instrument("source-a", _TypedSourceClient)
    assert_type(source, InstrumentRef[_TypedSourceClient])

    handle = LabInstrumentOperations(
        daemon,
        operator="test",
    ).open(source)

    client = handle[source]
    assert_type(client, _TypedSourceClient)
    assert isinstance(client.session, InstrumentClientChannel)
    assert client.instrument_id == "source-a"
    assert handle.instrument_ids == ("source-a",)
    assert daemon.open_commands == []
    daemon.close()


def test_typed_instrument_ref_validates_required_capabilities_when_bound() -> None:
    required = InterfaceRef("test.source/v1")
    source = instrument(
        "source-a",
        _TypedSourceClient,
        requires=(required,),
    )
    assert source.requires == (required,)

    supported_daemon = _ConfiguredDefaultsDaemon(
        interface_ids=(required.interface_id,),
    )
    supported = LabInstrumentOperations(
        supported_daemon,
        operator="test",
    ).open(source)
    assert isinstance(supported[source], _TypedSourceClient)
    supported.close()
    supported_daemon.close()

    unsupported_daemon = _ConfiguredDefaultsDaemon()
    unsupported = LabInstrumentOperations(
        unsupported_daemon,
        operator="test",
    ).open(source)
    with pytest.raises(
        ValueError,
        match=r"source-a.*required interfaces.*test.source/v1",
    ):
        unsupported[source]
    unsupported.close()
    unsupported_daemon.close()


def test_typed_instrument_ref_selects_an_exact_interface_mount() -> None:
    required = InterfaceRef("test.source/v1")
    selected = instrument(
        "source-a",
        _TypedSourceClient,
        requires=(required,),
        component_path=("channels", "2"),
    )
    wrong_mount = instrument(
        "source-a",
        _TypedSourceClient,
        requires=(required,),
        component_path=("channels", "3"),
    )
    daemon = _ConfiguredDefaultsDaemon(
        interface_ids=(required.interface_id,),
        interface_mounts=(
            (required.interface_id, ("channels", "1")),
            (required.interface_id, ("channels", "2")),
        ),
        components=(
            instrument_component(
                "channels",
                components=(
                    instrument_component("1"),
                    instrument_component("2"),
                ),
            ),
        ),
    )
    handle = LabInstrumentOperations(daemon, operator="test").open(selected)

    client = handle[selected]
    assert client.session.component_path == ("channels", "2")
    with pytest.raises(
        ValueError,
        match=r"source-a.*required interfaces.*test.source/v1",
    ):
        handle[wrong_mount]
    with pytest.raises(TypeError, match="whole physical instrument"):
        client.session.apply_configured_defaults("source-a")

    handle.close()
    daemon.close()


def test_typed_instrument_ref_must_belong_to_the_session() -> None:
    daemon = _ConfiguredDefaultsDaemon()
    source = instrument("source-a", _TypedSourceClient)
    other = instrument("source-b", _TypedSourceClient)
    handle = LabInstrumentOperations(
        daemon,
        operator="test",
    ).open(source)

    with pytest.raises(ValueError, match="is not in this session"):
        handle[other]

    assert daemon.open_commands == []
    daemon.close()


def test_session_handle_immediately_renews_a_late_open_lease() -> None:
    daemon = _HeartbeatDaemon(
        lease_duration=timedelta(seconds=30),
        initial_renewed_at=datetime.now(UTC) - timedelta(seconds=15),
    )
    handle = InstrumentSessionHandle(
        client=daemon,
        instrument_ids=("source-a",),
        actor="test",
    )

    try:
        handle._observed_state()

        assert daemon.renew_attempted.wait(timeout=5)
    finally:
        handle.close()
        daemon.close()


def test_session_handle_close_stops_heartbeat() -> None:
    daemon = _HeartbeatDaemon()
    handle = InstrumentSessionHandle(
        client=daemon,
        instrument_ids=("source-a",),
        actor="test",
    )

    try:
        handle._observed_state()
        heartbeat = handle._heartbeat
        assert heartbeat is not None
        assert daemon.renew_attempted.wait(timeout=5)

        receipt = handle.close()

        assert receipt is not None
        assert receipt.status == "closed"
        assert not heartbeat._thread.is_alive()
    finally:
        handle.close()
        daemon.close()


def test_session_handle_surfaces_renewal_failure() -> None:
    failure = RuntimeError("renewal transport failed")
    daemon = _HeartbeatDaemon(renew_error=failure)
    handle = InstrumentSessionHandle(
        client=daemon,
        instrument_ids=("source-a",),
        actor="test",
    )

    try:
        handle._observed_state()
        heartbeat = handle._heartbeat
        assert heartbeat is not None
        assert daemon.renew_attempted.wait(timeout=5)
        heartbeat._thread.join(timeout=5)

        with pytest.raises(
            RuntimeError,
            match="instrument session lease renewal failed",
        ) as caught:
            handle._read_state()

        assert caught.value.__cause__ is failure
    finally:
        handle.close()
        daemon.close()


def test_session_handle_keeps_heartbeat_after_close_failure() -> None:
    close_error = RuntimeError("close transport failed")
    daemon = _HeartbeatDaemon(close_error=close_error)
    handle = InstrumentSessionHandle(
        client=daemon,
        instrument_ids=("source-a",),
        actor="test",
    )

    try:
        handle._observed_state()
        heartbeat = handle._heartbeat
        assert heartbeat is not None

        with pytest.raises(RuntimeError, match="close transport failed") as caught:
            handle.close()

        assert caught.value is close_error
        daemon.renew_attempted.clear()
        assert daemon.renew_attempted.wait(timeout=5)
        assert heartbeat._thread.is_alive()

        receipt = handle.close()

        assert receipt is not None
        assert receipt.status == "closed"
        assert daemon.close_calls == 2
        assert not heartbeat._thread.is_alive()
    finally:
        handle.close()
        daemon.close()


def test_discarded_session_handle_requests_heartbeat_stop() -> None:
    daemon = _HeartbeatDaemon()
    handle = InstrumentSessionHandle(
        client=daemon,
        instrument_ids=("source-a",),
        actor="test",
    )
    handle._observed_state()
    heartbeat = handle._heartbeat
    assert heartbeat is not None
    handle_reference = ref(handle)

    del handle
    collect_garbage()
    heartbeat._thread.join(timeout=5)

    assert handle_reference() is None
    assert not heartbeat._thread.is_alive()
    daemon.close()


def test_apply_configured_defaults_lazily_opens_and_generates_operation_id() -> None:
    daemon = _ConfiguredDefaultsDaemon()
    handle = InstrumentSessionHandle(
        client=daemon,
        instrument_ids=("source-a",),
        actor="test",
    )

    try:
        assert daemon.open_commands == []

        receipt = handle._apply_configured_defaults()

        assert len(daemon.open_commands) == 1
        [(session_id, instrument_id, command)] = daemon.apply_calls
        assert session_id == "session-1"
        assert instrument_id == "source-a"
        assert command.operation_id.startswith(
            "interactive.configured_defaults.source-a."
        )
        assert receipt.operation_id == command.operation_id
    finally:
        daemon.close()


def test_session_handle_exposes_opening_observation_without_refresh() -> None:
    description = InstrumentDescription(
        instrument_id="source-a",
        implementation_id="tests.source",
        implementation_version="1",
    )
    state = InstrumentStateSnapshot(instrument_id="source-a")
    daemon = _CollectingDaemon(description, state)
    handle = InstrumentSessionHandle(
        client=daemon,
        instrument_ids=("source-a",),
        actor="test",
    )

    try:
        observed = handle._observed_state()

        assert observed == state
        assert observed is not state
        assert daemon.state_reads == 0
        assert handle._read_state() == state
        assert daemon.state_reads == 1
    finally:
        daemon.close()


def test_apply_configured_defaults_requires_multi_instrument_selection() -> None:
    daemon = _ConfiguredDefaultsDaemon()
    handle = InstrumentSessionHandle(
        client=daemon,
        instrument_ids=("source-a", "source-b"),
        actor="test",
    )

    try:
        with pytest.raises(
            ValueError,
            match="multi-instrument sessions require an instrument_id",
        ):
            handle._apply_configured_defaults()

        assert daemon.open_commands == []

        receipt = handle._apply_configured_defaults(instrument_id="source-b")

        assert receipt.instrument_id == "source-b"
        [(_, instrument_id, _)] = daemon.apply_calls
        assert instrument_id == "source-b"
        assert daemon.open_commands[0].instrument_ids == ("source-a", "source-b")
    finally:
        daemon.close()


def test_notebook_collect_sends_unspecified_results_without_reading_state() -> None:
    description = InstrumentDescription(
        instrument_id="bias",
        implementation_id="tests.source",
        implementation_version="1",
    )
    daemon = _CollectingDaemon(
        description,
        InstrumentStateSnapshot(instrument_id="bias"),
    )
    handle = InstrumentSessionHandle(
        client=daemon,
        instrument_ids=("bias",),
        actor="test",
    )

    try:
        receipt = handle._collect(DC_MONITOR_MEASURE_CURRENT)
    finally:
        daemon.close()

    assert receipt.status == "collected"
    assert daemon.state_reads == 0
    assert daemon.collect_intent is not None
    assert daemon.collect_intent.command_id.startswith("interactive.collect.bias.")
    assert daemon.collect_intent == InteractiveCollectIntent(
        command_id=daemon.collect_intent.command_id,
        instrument_id="bias",
        interface_id=DC_MONITOR_MEASURE_CURRENT.interface_id,
        component_path=list(DC_MONITOR_MEASURE_CURRENT.component_path),
        acquisition_id=DC_MONITOR_MEASURE_CURRENT.acquisition_id,
        result_ids=[],
    )


def test_notebook_collect_sends_explicit_result_identity() -> None:
    description = InstrumentDescription(
        instrument_id="bias",
        implementation_id="tests.source",
        implementation_version="1",
    )
    daemon = _CollectingDaemon(
        description,
        InstrumentStateSnapshot(instrument_id="bias"),
    )
    handle = InstrumentSessionHandle(
        client=daemon,
        instrument_ids=("bias",),
        actor="test",
    )

    try:
        handle._collect(
            DC_MONITOR_MEASURE_CURRENT,
            DC_MONITOR_CURRENT_RESULT,
        )
    finally:
        daemon.close()

    assert daemon.state_reads == 0
    assert daemon.collect_intent is not None
    assert daemon.collect_intent.result_ids == [DC_MONITOR_CURRENT_RESULT.result_id]


def test_declared_live_client_rejects_incomplete_provider_readback() -> None:
    description = InstrumentDescription(
        instrument_id="readout",
        implementation_id="tests.network_sweep",
        implementation_version="1",
        interfaces=[network_sweep_interface()],
    )
    daemon = _CollectingDaemon(
        description,
        InstrumentStateSnapshot(instrument_id="readout"),
    )
    target = network_sweep("readout")
    handle = LabInstrumentOperations(daemon, operator="test").open(target)

    try:
        with pytest.raises(
            ProviderContractError,
            match="missing requested result 'frequency'",
        ) as raised:
            handle[target].sweep()
    finally:
        handle.close()
        daemon.close()

    assert [item.code for item in raised.value.problems] == [
        "instrument_collect_result_missing",
        "instrument_collect_result_missing",
    ]
    assert daemon.collect_intent is not None
    assert daemon.collect_intent.acquisition_id == (
        NETWORK_SWEEP_ACQUISITION.acquisition_id
    )
    assert daemon.collect_intent.result_ids == [
        NETWORK_SWEEP_FREQUENCY_RESULT.result_id,
        NETWORK_SWEEP_S_PARAMETER_RESULT.result_id,
    ]


@pytest.mark.parametrize(
    ("receipt", "certainty"),
    (
        pytest.param(
            CollectReceipt(
                status="not_collected",
                problems=(
                    problem(
                        "instrument_unavailable",
                        "instrument was unavailable",
                        phase=ProblemPhase.EXECUTION,
                    ),
                ),
            ),
            "known",
            id="not-collected",
        ),
        pytest.param(
            CollectReceipt(
                status="unknown",
                problems=(
                    problem(
                        "instrument_outcome_unknown",
                        "instrument outcome is unknown",
                        phase=ProblemPhase.EXECUTION,
                    ),
                ),
            ),
            "indeterminate",
            id="unknown",
        ),
    ),
)
def test_declared_live_client_raises_structured_collection_failure(
    receipt: CollectReceipt,
    certainty: str,
) -> None:
    description = InstrumentDescription(
        instrument_id="thermometer",
        implementation_id="tests.temperature_readout",
        implementation_version="1",
        interfaces=[temperature_readout_interface()],
    )
    daemon = _CollectingDaemon(
        description,
        InstrumentStateSnapshot(instrument_id="thermometer"),
        collect_receipt=receipt,
    )
    target = temperature_readout("thermometer")
    handle = LabInstrumentOperations(daemon, operator="test").open(target)

    try:
        with pytest.raises(InstrumentCollectFailure) as raised:
            handle[target].sample()
    finally:
        handle.close()
        daemon.close()

    assert raised.value.receipt is receipt
    assert raised.value.certainty == certainty


def test_generated_temperature_client_collects_and_maps_named_results() -> None:
    temperature = MeasurementScalar.create(value=0.12, unit="K")
    resistance = MeasurementScalar.create(value=842.0, unit="Ohm")
    description = InstrumentDescription(
        instrument_id="thermometer",
        implementation_id="tests.temperature_readout",
        implementation_version="1",
        interfaces=[temperature_readout_interface()],
    )
    daemon = _CollectingDaemon(
        description,
        InstrumentStateSnapshot(instrument_id="thermometer"),
        readback=InstrumentReadback(
            values={
                TEMPERATURE_READOUT_TEMPERATURE_RESULT.result_id: temperature,
                TEMPERATURE_READOUT_RESISTANCE_RESULT.result_id: resistance,
            }
        ),
    )
    target = temperature_readout("thermometer")
    handle = LabInstrumentOperations(daemon, operator="test").open(target)

    try:
        readback = assert_type(handle[target].sample(), TemperatureReadback)
    finally:
        handle.close()
        daemon.close()

    assert readback.receipt.status == "collected"
    assert readback.temperature == temperature
    assert readback.resistance == resistance
    assert daemon.collect_intent is not None
    assert daemon.collect_intent.interface_id == TEMPERATURE_READOUT_SAMPLE.interface_id
    assert daemon.collect_intent.component_path == list(
        TEMPERATURE_READOUT_SAMPLE.component_path
    )
    assert (
        daemon.collect_intent.acquisition_id
        == TEMPERATURE_READOUT_SAMPLE.acquisition_id
    )
    assert daemon.collect_intent.result_ids == [
        TEMPERATURE_READOUT_TEMPERATURE_RESULT.result_id,
        TEMPERATURE_READOUT_RESISTANCE_RESULT.result_id,
    ]


def test_notebook_collect_rejects_a_result_from_another_acquisition() -> None:
    description = InstrumentDescription(
        instrument_id="bias",
        implementation_id="tests.source",
        implementation_version="1",
    )
    daemon = _CollectingDaemon(
        description,
        InstrumentStateSnapshot(instrument_id="bias"),
    )
    handle = InstrumentSessionHandle(
        client=daemon,
        instrument_ids=("bias",),
        actor="test",
    )

    try:
        with pytest.raises(
            ValueError,
            match="collect results must belong to the selected acquisition",
        ):
            handle._collect(
                DC_MONITOR_MEASURE_CURRENT,
                NETWORK_SWEEP_FREQUENCY_RESULT,
            )
    finally:
        daemon.close()

    assert daemon.collect_intent is None


def test_notebook_invoke_rejects_argument_from_another_operation() -> None:
    description = InstrumentDescription(
        instrument_id="source",
        implementation_id="tests.source",
        implementation_version="1",
    )
    daemon = _CollectingDaemon(
        description,
        InstrumentStateSnapshot(instrument_id="source"),
    )
    handle = InstrumentSessionHandle(
        client=daemon,
        instrument_ids=("source",),
        actor="test",
    )
    operation = InterfaceRef("test.play_program/v1").operation("play")
    unrelated = (
        InterfaceRef("test.play_program/v1").operation("preview").argument("program")
    )

    try:
        with pytest.raises(
            ValueError,
            match="arguments must belong to the selected operation",
        ):
            handle._invoke(operation, {unrelated: False})
    finally:
        daemon.close()
