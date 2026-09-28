from __future__ import annotations

from collections.abc import Callable
from threading import Event, Lock, Thread
from typing import override
from uuid import uuid4

import pytest
from scopecat.kernel.problems import Problem, ProblemPhase
from scopecat.records.config import InstrumentBindingSpec
from scopecat.records.instrument import (
    InstrumentStateReadback,
    InstrumentStateSnapshot,
    state_member_target,
)
from scopecat.sdk.instruments import (
    AcquisitionPreparationReceipt,
    ApplyReceipt,
    CollectReceipt,
    DriverAcquisition,
    DriverOutcome,
    DriverReadback,
    DriverRejected,
    DriverStateReadback,
    DriverStateReadRequest,
    InstrumentDescription,
    InstrumentProviderDescription,
    InterfaceRef,
    InvokeReceipt,
    PropertyRef,
    capture_state_members,
    operation,
)
from scopecat.sdk.instruments.backend import (
    BackendAcquisitionPlan,
    BackendApplyRequest,
    BackendCollectRequest,
    BackendCollectResult,
    BackendInvokeRequest,
    BackendReadRequest,
    BackendStateMemberWrite,
    decode_driver_operation,
)
from scopecat.sdk.instruments.catalog import DriverCatalog
from scopecat.sdk.instruments.driver_adapter import (
    lower_acquisition,
    lower_state_patch,
    lower_state_read_request,
    project_apply_outcome,
    project_collect_outcome,
    project_invoke_outcome,
    project_state_readback,
)
from scopecat.sdk.payloads import EMPTY_PAYLOAD_CODECS, PayloadCodecCatalog
from scopecat_testkit.instrument_drivers import (
    SignalInstrumentDriver,
    number_state,
)

from scopecat_server.instruments.actors import (
    InstrumentActorConflict,
    InstrumentActorRegistry,
    InstrumentActorShutdown,
    InstrumentBindingKey,
    InstrumentOwnerKey,
    OwnedInstrument,
)
from scopecat_server.instruments.backend import (
    ConnectedInstrument,
    InstrumentBackendEndpoint,
    InstrumentHandle,
    InstrumentHandleInvalid,
)


class _TrackingDriver(SignalInstrumentDriver):
    def __init__(self, instrument_id: str) -> None:
        super().__init__(instrument_id=instrument_id)
        self.abort_count = 0
        self.disconnect_count = 0

    def change_from_front_panel(self, value: float) -> None:
        self._state[("test.set_gain/v1", "gain")] = value

    @override
    def disconnect(self) -> None:
        self.disconnect_count += 1

    @override
    def abort(self) -> None:
        self.abort_count += 1


class _DisconnectFailDriver(_TrackingDriver):
    @override
    def disconnect(self) -> None:
        self.disconnect_count += 1
        raise RuntimeError("disconnect failed")


class _RejectCollectDriver(_TrackingDriver):
    @override
    def collect(
        self,
        request: DriverAcquisition,
    ) -> DriverOutcome[DriverReadback]:
        del request
        return DriverRejected(
            problems=(
                Problem(
                    code="test_collect_rejected",
                    phase=ProblemPhase.EXECUTION,
                    message="collection rejected",
                ),
            ),
        )


class _InvalidatingInvokeDriver(_TrackingDriver):
    @override
    def describe(self) -> InstrumentDescription:
        description = super().describe()
        return description.model_copy(
            update={
                "interfaces": [
                    mounted
                    if mounted.id != "test.play_program/v1"
                    else mounted.model_copy(
                        update={
                            "operations": [
                                operation(
                                    "play",
                                    invalidates=[
                                        PropertyRef(
                                            "test.set_frequency/v1",
                                            (),
                                            "frequency",
                                        )
                                    ],
                                )
                            ]
                        }
                    )
                    for mounted in description.interfaces
                ]
            }
        )


class _BlockingReadDriver(_TrackingDriver):
    def __init__(self, instrument_id: str) -> None:
        super().__init__(instrument_id)
        self.first_entered = Event()
        self.release_first = Event()
        self._counter_lock = Lock()
        self._call_count = 0
        self._active = 0
        self.max_active = 0

    @override
    def read_state(self, request: DriverStateReadRequest) -> DriverStateReadback:
        with self._counter_lock:
            self._call_count += 1
            call_number = self._call_count
            self._active += 1
            self.max_active = max(self.max_active, self._active)
        try:
            if call_number == 1:
                self.first_entered.set()
                assert self.release_first.wait(timeout=10)
            return super().read_state(request)
        finally:
            with self._counter_lock:
                self._active -= 1


class _DriverEndpoint(InstrumentBackendEndpoint):
    def __init__(
        self,
        drivers: list[_TrackingDriver],
        instrument_id: str = "source-0",
        *,
        driver_type: type[_TrackingDriver] = _TrackingDriver,
    ) -> None:
        self._drivers = drivers
        self._instrument_id = instrument_id
        self._driver_type = driver_type
        self._endpoint_id = uuid4().hex
        self._connections: dict[InstrumentHandle, _TrackingDriver] = {}
        self._lock = Lock()

    @property
    @override
    def healthy(self) -> bool:
        return True

    @property
    @override
    def provider_id(self) -> str:
        return "tests.actor_provider"

    @property
    @override
    def driver_catalog(self) -> DriverCatalog:
        return DriverCatalog(provider_id=self.provider_id)

    @property
    @override
    def payload_catalog(self) -> PayloadCodecCatalog:
        return EMPTY_PAYLOAD_CODECS.catalog

    @override
    def describe(
        self,
        bindings: tuple[InstrumentBindingSpec, ...],
    ) -> InstrumentProviderDescription:
        del bindings
        raise AssertionError("actor tests do not resolve instrument contracts")

    @override
    def connect(
        self,
        *,
        binding: InstrumentBindingSpec,
        expected: InstrumentDescription,
    ) -> ConnectedInstrument:
        connected = self.attach(self._driver_type(binding.id))
        assert connected.description == expected
        return connected

    def __call__(self) -> ConnectedInstrument:
        return self.attach(self._driver_type(self._instrument_id))

    def attach(self, driver: _TrackingDriver) -> ConnectedInstrument:
        handle = InstrumentHandle(
            endpoint_id=self._endpoint_id,
            token=uuid4().hex,
        )
        with self._lock:
            self._connections[handle] = driver
            self._drivers.append(driver)
        return ConnectedInstrument(handle=handle, description=driver.describe())

    @override
    def read_state(
        self,
        handle: InstrumentHandle,
        request: BackendReadRequest,
    ) -> InstrumentStateReadback:
        driver = self._driver(handle)
        return project_state_readback(
            driver.instrument_id,
            driver.read_state(lower_state_read_request(request)),
        )

    @override
    def apply_state(
        self,
        handle: InstrumentHandle,
        request: BackendApplyRequest,
    ) -> ApplyReceipt:
        driver = self._driver(handle)
        return project_apply_outcome(
            driver.instrument_id,
            driver.apply_state(lower_state_patch(request)),
        )

    @override
    def invoke(
        self,
        handle: InstrumentHandle,
        request: BackendInvokeRequest,
    ) -> InvokeReceipt:
        driver = self._driver(handle)
        operation = decode_driver_operation(request, EMPTY_PAYLOAD_CODECS)
        return project_invoke_outcome(
            driver.instrument_id,
            driver.invoke(operation),
        )

    @override
    def collect(
        self,
        handle: InstrumentHandle,
        request: BackendCollectRequest,
    ) -> CollectReceipt:
        return project_collect_outcome(
            request,
            self._driver(handle).collect(lower_acquisition(request)),
        )

    @override
    def prepare_acquisitions(
        self,
        handle: InstrumentHandle,
        plan: BackendAcquisitionPlan,
    ) -> AcquisitionPreparationReceipt:
        del handle, plan
        return AcquisitionPreparationReceipt()

    @override
    def abort(self, handle: InstrumentHandle) -> None:
        self._driver(handle).abort()

    @override
    def disconnect(self, handle: InstrumentHandle) -> None:
        with self._lock:
            driver = self._connections.pop(handle)
        driver.disconnect()

    @override
    def shutdown(self) -> None:
        with self._lock:
            drivers = tuple(self._connections.values())
            self._connections.clear()
        for driver in drivers:
            driver.disconnect()

    def _driver(self, handle: InstrumentHandle) -> _TrackingDriver:
        if handle.endpoint_id != self._endpoint_id:
            raise InstrumentHandleInvalid("foreign instrument handle")
        with self._lock:
            try:
                return self._connections[handle]
            except KeyError as error:
                raise InstrumentHandleInvalid("stale instrument handle") from error


def _binding(
    binding_fingerprint: str = "binding-a",
    *,
    contract_fingerprint: str = "contract-a",
) -> InstrumentBindingKey:
    return InstrumentBindingKey(
        provider_id="tests.actor_provider",
        binding_fingerprint=binding_fingerprint,
        contract_fingerprint=contract_fingerprint,
    )


def _owner(
    owner_id: str,
    *,
    kind: str = "instrument_session",
) -> InstrumentOwnerKey:
    if kind == "run":
        return InstrumentOwnerKey(kind="run", owner_id=owner_id, fence="lease-1")
    return InstrumentOwnerKey(kind="instrument_session", owner_id=owner_id)


def _exclusivity_key(instrument_id: str) -> str:
    return f"tests.actor:{instrument_id}"


def _endpoint(
    drivers: list[_TrackingDriver],
    instrument_id: str = "source-0",
    *,
    driver_type: type[_TrackingDriver] = _TrackingDriver,
) -> _DriverEndpoint:
    return _DriverEndpoint(
        drivers,
        instrument_id,
        driver_type=driver_type,
    )


def _capture_request(description: InstrumentDescription) -> BackendReadRequest:
    return BackendReadRequest(
        targets=tuple(
            state_member_target(target) for target in capture_state_members(description)
        )
    )


def _read_capture(owned: OwnedInstrument) -> InstrumentStateReadback:
    return owned.read_state(_capture_request(owned.description))


def test_release_reuses_connection_but_requires_fresh_observation() -> None:
    registry = InstrumentActorRegistry()
    drivers: list[_TrackingDriver] = []
    endpoint = _endpoint(drivers)

    first = registry.acquire(
        _exclusivity_key("source-0"),
        "source-0",
        binding=_binding(),
        owner=_owner("session-1"),
        endpoint=endpoint,
        connect=endpoint,
    )
    assert not first.reused_connection
    targets = _capture_request(first.description).targets
    empty_cache = first.state_cache(targets)
    assert empty_cache.generation == 0
    assert {entry.status for entry in empty_cache.entries} == {"unobserved"}
    assert {entry.reason for entry in empty_cache.entries} == {"not_observed"}
    observed = _read_capture(first)
    assert first.assumed_state is None
    unconfirmed_cache = first.state_cache(targets)
    assert unconfirmed_cache.generation == 1
    assert {entry.status for entry in unconfirmed_cache.entries} == {"unknown"}
    assert {entry.reason for entry in unconfirmed_cache.entries} == {
        "state_read_unconfirmed"
    }
    first.adopt_readback(observed)
    confirmed_cache = first.state_cache(targets)
    assert confirmed_cache.generation == 2
    assert {entry.status for entry in confirmed_cache.entries} == {"observed"}
    assert all(entry.observation is not None for entry in confirmed_cache.entries)
    first.release()

    assert first.assumed_state is None
    assert drivers[0].disconnect_count == 0
    drivers[0].change_from_front_panel(7.0)

    second = registry.acquire(
        _exclusivity_key("source-0"),
        "source-0",
        binding=_binding(),
        owner=_owner("session-2"),
        endpoint=endpoint,
        connect=endpoint,
    )
    assert second.reused_connection
    assert len(drivers) == 1
    refreshed = _read_capture(second)
    gain = next(
        item for item in refreshed.observations if item.target.property_id == "gain"
    )
    assert gain.value == number_state(7.0)
    assert second.assumed_state is None
    second.adopt_readback(refreshed)
    second.release()
    registry.shutdown()

    assert drivers[0].disconnect_count == 1


def test_validated_empty_readback_establishes_an_empty_baseline() -> None:
    registry = InstrumentActorRegistry()
    endpoint = _endpoint([])
    owned = registry.acquire(
        _exclusivity_key("source-0"),
        "source-0",
        binding=_binding(),
        owner=_owner("session-1"),
        endpoint=endpoint,
        connect=endpoint,
    )

    owned.adopt_readback(InstrumentStateReadback(instrument_id="source-0"))

    assert owned.assumed_state == InstrumentStateSnapshot(instrument_id="source-0")
    owned.release()
    registry.shutdown()


def test_logical_rename_reuses_the_actor_for_the_same_exclusive_resource() -> None:
    registry = InstrumentActorRegistry()
    drivers: list[_TrackingDriver] = []
    endpoint = _endpoint(drivers)
    exclusivity_key = _exclusivity_key("physical-source")
    first = registry.acquire(
        exclusivity_key,
        "source-before-rename",
        binding=_binding(),
        owner=_owner("session-1"),
        endpoint=endpoint,
        connect=endpoint,
    )
    first.release()

    second = registry.acquire(
        exclusivity_key,
        "source-after-rename",
        binding=_binding(),
        owner=_owner("session-2"),
        endpoint=endpoint,
        connect=endpoint,
    )

    assert second.instrument_id == "source-after-rename"
    assert second.reused_connection
    assert len(drivers) == 1
    second.release()
    registry.shutdown()


@pytest.mark.parametrize(
    "changed",
    [
        _binding("binding-b"),
        _binding(contract_fingerprint="contract-b"),
    ],
    ids=["binding", "contract"],
)
def test_key_change_is_rejected_while_owned(
    changed: InstrumentBindingKey,
) -> None:
    registry = InstrumentActorRegistry()
    drivers: list[_TrackingDriver] = []
    endpoint = _endpoint(drivers)
    first = registry.acquire(
        _exclusivity_key("source-0"),
        "source-0",
        binding=_binding(),
        owner=_owner("session-1"),
        endpoint=endpoint,
        connect=endpoint,
    )

    with pytest.raises(
        InstrumentActorConflict,
        match="cannot change backend binding",
    ):
        registry.acquire(
            _exclusivity_key("source-0"),
            "source-after-rename",
            binding=changed,
            owner=_owner("session-2"),
            endpoint=endpoint,
            connect=endpoint,
        )
    assert len(drivers) == 1
    assert drivers[0].disconnect_count == 0

    first.release()
    registry.shutdown()


@pytest.mark.parametrize(
    "changed",
    [
        _binding("binding-b"),
        _binding(contract_fingerprint="contract-b"),
    ],
    ids=["binding", "contract"],
)
def test_key_change_rebinds_idle_connection(
    changed: InstrumentBindingKey,
) -> None:
    registry = InstrumentActorRegistry()
    drivers: list[_TrackingDriver] = []
    endpoint = _endpoint(drivers)
    first = registry.acquire(
        _exclusivity_key("source-0"),
        "source-0",
        binding=_binding(),
        owner=_owner("session-1"),
        endpoint=endpoint,
        connect=endpoint,
    )
    first.release()

    second = registry.acquire(
        _exclusivity_key("source-0"),
        "source-after-rename",
        binding=changed,
        owner=_owner("session-2"),
        endpoint=endpoint,
        connect=endpoint,
    )
    assert not second.reused_connection
    assert second.instrument_id == "source-after-rename"
    assert len(drivers) == 2
    assert drivers[0].disconnect_count == 1
    second.release()
    registry.shutdown()


def test_endpoint_change_rebinds_idle_connection() -> None:
    registry = InstrumentActorRegistry()
    old_drivers: list[_TrackingDriver] = []
    new_drivers: list[_TrackingDriver] = []
    old_endpoint = _endpoint(old_drivers)
    new_endpoint = _endpoint(new_drivers)
    first = registry.acquire(
        _exclusivity_key("source-0"),
        "source-0",
        binding=_binding(),
        owner=_owner("session-1"),
        endpoint=old_endpoint,
        connect=old_endpoint,
    )
    first.release()

    second = registry.acquire(
        _exclusivity_key("source-0"),
        "source-0",
        binding=_binding(),
        owner=_owner("session-2"),
        endpoint=new_endpoint,
        connect=new_endpoint,
    )

    assert not second.reused_connection
    assert old_drivers[0].disconnect_count == 1
    assert len(new_drivers) == 1
    second.release()
    registry.shutdown()


def test_owner_epoch_rejects_stale_handles_and_fault_discards_connection() -> None:
    registry = InstrumentActorRegistry()
    drivers: list[_TrackingDriver] = []
    endpoint = _endpoint(drivers)
    first = registry.acquire(
        _exclusivity_key("source-0"),
        "source-0",
        binding=_binding(),
        owner=_owner("run-1", kind="run"),
        endpoint=endpoint,
        connect=endpoint,
    )
    first.adopt_readback(_read_capture(first))
    first.fault()

    assert drivers[0].disconnect_count == 1
    assert first.assumed_state is None
    with pytest.raises(InstrumentActorConflict, match="stale"):
        _read_capture(first)

    second = registry.acquire(
        _exclusivity_key("source-0"),
        "source-0",
        binding=_binding(),
        owner=_owner("run-2", kind="run"),
        endpoint=endpoint,
        connect=endpoint,
    )
    assert not second.reused_connection
    assert len(drivers) == 2
    assert second.epoch > first.epoch
    second.release()
    with pytest.raises(InstrumentActorConflict, match="stale"):
        second.invalidate_state()
    registry.shutdown()


def test_handle_serializes_driver_calls_and_never_exposes_the_driver() -> None:
    registry = InstrumentActorRegistry()
    drivers: list[_TrackingDriver] = []
    endpoint = _endpoint(
        drivers,
        driver_type=_BlockingReadDriver,
    )
    owned = registry.acquire(
        _exclusivity_key("source-0"),
        "source-0",
        binding=_binding(),
        owner=_owner("session-1"),
        endpoint=endpoint,
        connect=endpoint,
    )
    driver = drivers[0]
    assert isinstance(driver, _BlockingReadDriver)
    assert not hasattr(owned, "driver")

    errors: list[BaseException] = []
    second_attempted = Event()

    def first_read() -> None:
        try:
            _read_capture(owned)
        except BaseException as error:
            errors.append(error)

    def second_read() -> None:
        second_attempted.set()
        try:
            _read_capture(owned)
        except BaseException as error:
            errors.append(error)

    first_thread = Thread(target=first_read)
    second_thread = Thread(target=second_read)
    first_thread.start()
    assert driver.first_entered.wait(timeout=10)
    second_thread.start()
    assert second_attempted.wait(timeout=10)
    driver.release_first.set()
    first_thread.join(timeout=10)
    second_thread.join(timeout=10)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert not errors
    assert driver.max_active == 1
    owned.release()
    registry.shutdown()


def test_unrelated_instruments_can_connect_concurrently() -> None:
    registry = InstrumentActorRegistry()
    first_entered = Event()
    second_entered = Event()
    handles: list[OwnedInstrument] = []
    errors: list[BaseException] = []
    endpoint = _endpoint([])

    def connector(
        instrument_id: str,
        entered: Event,
        peer_entered: Event,
    ) -> Callable[[], ConnectedInstrument]:
        def connect() -> ConnectedInstrument:
            entered.set()
            assert peer_entered.wait(timeout=10)
            driver = _TrackingDriver(instrument_id)
            return endpoint.attach(driver)

        return connect

    def acquire(
        instrument_id: str,
        entered: Event,
        peer_entered: Event,
    ) -> None:
        try:
            handles.append(
                registry.acquire(
                    _exclusivity_key(instrument_id),
                    instrument_id,
                    binding=_binding(),
                    owner=_owner(f"session-{instrument_id}"),
                    endpoint=endpoint,
                    connect=connector(instrument_id, entered, peer_entered),
                )
            )
        except BaseException as error:
            errors.append(error)

    first_thread = Thread(
        target=acquire,
        args=("source-0", first_entered, second_entered),
    )
    second_thread = Thread(
        target=acquire,
        args=("source-1", second_entered, first_entered),
    )
    first_thread.start()
    second_thread.start()
    first_thread.join(timeout=10)
    second_thread.join(timeout=10)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert not errors
    assert len(handles) == 2
    for handle in handles:
        handle.release()
    registry.shutdown()


def test_handle_routes_driver_calls_through_one_owner_epoch() -> None:
    registry = InstrumentActorRegistry()
    drivers: list[_TrackingDriver] = []
    endpoint = _endpoint(drivers)
    owned = registry.acquire(
        _exclusivity_key("source-0"),
        "source-0",
        binding=_binding(),
        owner=_owner("session-1"),
        endpoint=endpoint,
        connect=endpoint,
    )
    observed = _read_capture(owned)
    owned.adopt_readback(observed)

    apply_request = BackendApplyRequest(
        assignments=(
            BackendStateMemberWrite(
                target=state_member_target(
                    InterfaceRef("test.set_gain/v1").property("gain")
                ),
                value=number_state(1.0),
            ),
        )
    )
    assert owned.apply_state(apply_request).status == "applied"
    [gain_cache] = owned.state_cache(
        (state_member_target(InterfaceRef("test.set_gain/v1").property("gain")),)
    ).entries
    assert gain_cache.status == "invalidated"
    assert gain_cache.reason == "state_applied"
    assert gain_cache.observation is None
    assert owned.assumed_state is not None
    assert {item.target.property_id for item in owned.assumed_state.observations} == {
        "frequency"
    }
    owned.adopt_readback(_read_capture(owned))

    invoke_request = BackendInvokeRequest(
        interface_id="test.play_program/v1",
        operation_id="play",
    )
    expected_driver_invoke = decode_driver_operation(
        invoke_request,
        EMPTY_PAYLOAD_CODECS,
    )
    invoke_receipt = owned.invoke(invoke_request)
    assert invoke_receipt.status == "invoked"
    assert owned.assumed_state is not None
    assert invoke_receipt.readback is None

    collect_request = BackendCollectRequest(
        interface_id="test.scalar_signal/v1",
        acquisition_id="sample",
        results=(BackendCollectResult(request_id="signal", result_id="signal"),),
    )
    assert owned.collect(collect_request).status == "collected"
    assert owned.assumed_state is not None
    assert drivers[0].applied == [lower_state_patch(apply_request)]
    assert drivers[0].invoked == [expected_driver_invoke]
    assert drivers[0].collect_requests == [lower_acquisition(collect_request)]

    owned.release()
    registry.shutdown()


def test_rejected_collection_invalidates_the_assumed_state() -> None:
    registry = InstrumentActorRegistry()
    drivers: list[_TrackingDriver] = []
    endpoint = _endpoint(drivers, driver_type=_RejectCollectDriver)
    owned = registry.acquire(
        _exclusivity_key("source-0"),
        "source-0",
        binding=_binding(),
        owner=_owner("session-1"),
        endpoint=endpoint,
        connect=endpoint,
    )
    owned.adopt_readback(_read_capture(owned))

    receipt = owned.collect(
        BackendCollectRequest(
            interface_id="test.scalar_signal/v1",
            acquisition_id="sample",
            results=(BackendCollectResult(request_id="signal", result_id="signal"),),
        )
    )

    assert receipt.status == "not_collected"
    assert owned.assumed_state is None
    cache = owned.state_cache(_capture_request(owned.description).targets)
    assert {entry.status for entry in cache.entries} == {"unknown"}
    assert {entry.reason for entry in cache.entries} == {"collect_outcome_unknown"}
    [uncached] = owned.state_cache(
        (state_member_target(InterfaceRef("test.set_gain/v1").property("other")),)
    ).entries
    assert uncached.status == "unknown"
    assert uncached.reason == "collect_outcome_unknown"
    assert uncached.generation == cache.generation
    owned.release()
    registry.shutdown()


def test_invoke_invalidates_only_declared_member_cache_entries() -> None:
    registry = InstrumentActorRegistry()
    drivers: list[_TrackingDriver] = []
    endpoint = _endpoint(drivers, driver_type=_InvalidatingInvokeDriver)
    owned = registry.acquire(
        _exclusivity_key("source-0"),
        "source-0",
        binding=_binding(),
        owner=_owner("session-1"),
        endpoint=endpoint,
        connect=endpoint,
    )
    owned.adopt_readback(_read_capture(owned))

    receipt = owned.invoke(
        BackendInvokeRequest(
            interface_id="test.play_program/v1",
            operation_id="play",
        )
    )

    assert receipt.status == "invoked"
    cache = owned.state_cache(_capture_request(owned.description).targets)
    by_property = {entry.target.property_id: entry for entry in cache.entries}
    assert by_property["frequency"].status == "invalidated"
    assert by_property["frequency"].reason == "operation_invalidated"
    assert by_property["gain"].status == "observed"
    owned.release()
    registry.shutdown()


def test_retirement_disconnects_and_removes_an_idle_actor() -> None:
    registry = InstrumentActorRegistry()
    drivers: list[_TrackingDriver] = []
    endpoint = _endpoint(drivers)
    exclusivity_key = _exclusivity_key("source-0")
    first = registry.acquire(
        exclusivity_key,
        "source-0",
        binding=_binding(),
        owner=_owner("session-1"),
        endpoint=endpoint,
        connect=endpoint,
    )
    first.release()

    with registry.begin_retirement((exclusivity_key,)) as retirement:
        with pytest.raises(InstrumentActorConflict, match="retiring"):
            registry.acquire(
                exclusivity_key,
                "source-0",
                binding=_binding(),
                owner=_owner("session-during-retirement"),
                endpoint=endpoint,
                connect=endpoint,
            )
        retirement.retire_idle()
        assert drivers[0].disconnect_count == 1

    retirement.release_gate()
    second = registry.acquire(
        exclusivity_key,
        "source-0",
        binding=_binding(),
        owner=_owner("session-2"),
        endpoint=endpoint,
        connect=endpoint,
    )

    assert not second.reused_connection
    assert len(drivers) == 2
    second.release()
    registry.shutdown()


def test_retirement_rejects_an_owned_actor_without_aborting_it() -> None:
    registry = InstrumentActorRegistry()
    drivers: list[_TrackingDriver] = []
    endpoint = _endpoint(drivers)
    exclusivity_key = _exclusivity_key("source-0")
    owned = registry.acquire(
        exclusivity_key,
        "source-0",
        binding=_binding(),
        owner=_owner("session-1"),
        endpoint=endpoint,
        connect=endpoint,
    )
    retirement = registry.begin_retirement((exclusivity_key,))

    with pytest.raises(InstrumentActorConflict, match="owned instrument"):
        retirement.retire_idle()

    assert _read_capture(owned).instrument_id == "source-0"
    assert drivers[0].abort_count == 0
    assert drivers[0].disconnect_count == 0
    owned.release()
    retirement.retire_idle()
    retirement.release_gate()
    registry.shutdown()

    assert drivers[0].abort_count == 0
    assert drivers[0].disconnect_count == 1


def test_retirement_catches_an_acquire_after_its_slow_connect() -> None:
    registry = InstrumentActorRegistry()
    drivers: list[_TrackingDriver] = []
    endpoint = _endpoint(drivers)
    exclusivity_key = _exclusivity_key("source-0")
    connect_entered = Event()
    release_connect = Event()
    acquired: list[OwnedInstrument] = []
    errors: list[BaseException] = []

    def slow_connect() -> ConnectedInstrument:
        connect_entered.set()
        assert release_connect.wait(timeout=10)
        return endpoint()

    def acquire() -> None:
        try:
            acquired.append(
                registry.acquire(
                    exclusivity_key,
                    "source-0",
                    binding=_binding(),
                    owner=_owner("session-before-retirement"),
                    endpoint=endpoint,
                    connect=slow_connect,
                )
            )
        except BaseException as error:
            errors.append(error)

    thread = Thread(target=acquire)
    thread.start()
    assert connect_entered.wait(timeout=10)
    retirement = registry.begin_retirement((exclusivity_key,))
    release_connect.set()

    retirement.retire_idle()
    thread.join(timeout=10)

    assert not thread.is_alive()
    assert acquired == []
    [error] = errors
    assert isinstance(error, InstrumentActorConflict)
    assert "retiring" in str(error)
    [driver] = drivers
    assert driver.disconnect_count == 1
    retirement.release_gate()
    registry.shutdown()


def test_retirement_disconnect_failure_leaves_a_terminal_actor() -> None:
    registry = InstrumentActorRegistry()
    drivers: list[_TrackingDriver] = []
    endpoint = _endpoint(drivers, driver_type=_DisconnectFailDriver)
    exclusivity_key = _exclusivity_key("source-0")
    owned = registry.acquire(
        exclusivity_key,
        "source-0",
        binding=_binding(),
        owner=_owner("session-1"),
        endpoint=endpoint,
        connect=endpoint,
    )
    owned.release()
    retirement = registry.begin_retirement((exclusivity_key,))

    with pytest.raises(RuntimeError, match="disconnect failed"):
        retirement.retire_idle()
    retirement.release_gate()
    retirement.release_gate()

    with pytest.raises(InstrumentActorShutdown, match="actor is shut down"):
        registry.acquire(
            exclusivity_key,
            "source-0",
            binding=_binding(),
            owner=_owner("session-2"),
            endpoint=endpoint,
            connect=endpoint,
        )
    assert len(drivers) == 1
    assert drivers[0].disconnect_count == 1
    registry.shutdown()


def test_stop_accepting_fences_new_owners_without_interrupting_the_drain() -> None:
    registry = InstrumentActorRegistry()
    drivers: list[_TrackingDriver] = []
    endpoint = _endpoint(drivers)
    owned = registry.acquire(
        _exclusivity_key("source-0"),
        "source-0",
        binding=_binding(),
        owner=_owner("session-1"),
        endpoint=endpoint,
        connect=endpoint,
    )
    registry.stop_accepting()

    owned.adopt_readback(_read_capture(owned))
    owned.release()
    assert drivers[0].disconnect_count == 0
    with pytest.raises(InstrumentActorShutdown, match="registry"):
        registry.acquire(
            _exclusivity_key("source-0"),
            "source-0",
            binding=_binding(),
            owner=_owner("session-2"),
            endpoint=endpoint,
            connect=endpoint,
        )

    registry.shutdown()
    assert drivers[0].disconnect_count == 1


def test_shutdown_invalidates_owned_handles_closes_every_actor_and_gates_acquire() -> (
    None
):
    registry = InstrumentActorRegistry()
    first_drivers: list[_TrackingDriver] = []
    second_drivers: list[_TrackingDriver] = []
    first_endpoint = _endpoint(first_drivers)
    second_endpoint = _endpoint(second_drivers, "source-1")
    first = registry.acquire(
        _exclusivity_key("source-0"),
        "source-0",
        binding=_binding(),
        owner=_owner("session-1"),
        endpoint=first_endpoint,
        connect=first_endpoint,
    )
    first.adopt_readback(_read_capture(first))
    second = registry.acquire(
        _exclusivity_key("source-1"),
        "source-1",
        binding=_binding(),
        owner=_owner("session-2"),
        endpoint=second_endpoint,
        connect=second_endpoint,
    )
    second.release()

    registry.shutdown()
    registry.shutdown()

    assert first.assumed_state is None
    assert first_drivers[0].disconnect_count == 1
    assert second_drivers[0].disconnect_count == 1
    with pytest.raises(InstrumentActorConflict, match="stale"):
        _read_capture(first)
    with pytest.raises(InstrumentActorShutdown, match="registry"):
        third_endpoint = _endpoint([])
        registry.acquire(
            _exclusivity_key("source-2"),
            "source-2",
            binding=_binding(),
            owner=_owner("session-3"),
            endpoint=third_endpoint,
            connect=third_endpoint,
        )
