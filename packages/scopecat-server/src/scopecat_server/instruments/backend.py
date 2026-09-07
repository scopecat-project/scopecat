"""Opaque runtime endpoint for instrument drivers."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from threading import RLock
from typing import Protocol
from uuid import uuid4

from scopecat.kernel.problems import Problem, ProblemPhase, model_location, problem
from scopecat.planning.provider_validation import (
    describe_instruments,
    validate_instruments,
)
from scopecat.records.config import InstrumentBindingSpec
from scopecat.records.instrument import InstrumentStateReadback
from scopecat.sdk.instruments.backend import (
    BackendAcquisitionPlan,
    BackendApplyRequest,
    BackendCollectRequest,
    BackendInvokeRequest,
    BackendReadRequest,
    InstrumentBackend,
    decode_driver_operation,
)
from scopecat.sdk.instruments.catalog import DriverCatalog
from scopecat.sdk.instruments.commands import (
    AcquisitionPreparationReceipt,
    ApplyReceipt,
    CollectReceipt,
    InvokeReceipt,
)
from scopecat.sdk.instruments.contracts import InstrumentDescription
from scopecat.sdk.instruments.driver_adapter import (
    lower_acquisition,
    lower_acquisition_plan,
    lower_state_patch,
    lower_state_read_request,
    project_acquisition_preparation_outcome,
    project_apply_outcome,
    project_collect_outcome,
    project_invoke_outcome,
    project_state_readback,
)
from scopecat.sdk.instruments.provider import (
    AcquisitionPreparer,
    DriverFault,
    InstrumentConnectionContext,
    InstrumentDriver,
    InstrumentProviderContext,
    InstrumentProviderDescription,
)
from scopecat.sdk.payloads import PayloadCodecCatalog


class InstrumentBackendError(RuntimeError):
    """Base error for backend endpoint failures."""

    def __init__(
        self,
        message: str,
        *,
        diagnostic: dict[str, str | int] | None = None,
        problems: tuple[Problem, ...] = (),
    ) -> None:
        self.problems = problems
        self.diagnostic = diagnostic
        super().__init__(message)


class InstrumentBackendRejected(InstrumentBackendError):
    def __init__(
        self,
        message: str,
        *,
        problems: tuple[Problem, ...],
        diagnostic: dict[str, str | int] | None = None,
    ) -> None:
        self.problems = problems
        super().__init__(message, diagnostic=diagnostic, problems=problems)


class InstrumentBackendUnavailable(InstrumentBackendError):
    """The backend cannot create or serve an instrument handle."""


class InstrumentHandleInvalid(InstrumentBackendError):
    """A handle is stale or belongs to another endpoint generation."""


@dataclass(frozen=True, slots=True)
class InstrumentHandle:
    endpoint_id: str
    token: str


@dataclass(frozen=True, slots=True)
class ConnectedInstrument:
    handle: InstrumentHandle
    description: InstrumentDescription


class InstrumentBackendEndpoint(Protocol):
    @property
    def healthy(self) -> bool: ...

    @property
    def provider_id(self) -> str: ...

    @property
    def driver_catalog(self) -> DriverCatalog: ...

    @property
    def payload_catalog(self) -> PayloadCodecCatalog: ...

    def describe(
        self,
        bindings: tuple[InstrumentBindingSpec, ...],
    ) -> InstrumentProviderDescription: ...

    def probe(self, binding: InstrumentBindingSpec) -> InstrumentDescription: ...

    def connect(
        self,
        *,
        binding: InstrumentBindingSpec,
        expected: InstrumentDescription,
    ) -> ConnectedInstrument: ...

    def read_state(
        self,
        handle: InstrumentHandle,
        request: BackendReadRequest,
    ) -> InstrumentStateReadback: ...

    def apply_state(
        self,
        handle: InstrumentHandle,
        request: BackendApplyRequest,
    ) -> ApplyReceipt: ...

    def invoke(
        self,
        handle: InstrumentHandle,
        request: BackendInvokeRequest,
    ) -> InvokeReceipt: ...

    def collect(
        self,
        handle: InstrumentHandle,
        request: BackendCollectRequest,
    ) -> CollectReceipt: ...

    def prepare_acquisitions(
        self,
        handle: InstrumentHandle,
        plan: BackendAcquisitionPlan,
    ) -> AcquisitionPreparationReceipt: ...

    def abort(self, handle: InstrumentHandle) -> None: ...

    def disconnect(self, handle: InstrumentHandle) -> None: ...

    def shutdown(self) -> None: ...


@dataclass(slots=True)
class _LocalConnection:
    driver: InstrumentDriver
    lock: RLock


class LocalInstrumentBackendEndpoint:
    """Own raw drivers behind handles and serialize provider entry points."""

    def __init__(self, backend: InstrumentBackend) -> None:
        self._provider = backend.provider
        self._driver_catalog = backend.driver_catalog
        self._payload_codecs = backend.payload_codecs
        self._endpoint_id = uuid4().hex
        self._connections: dict[InstrumentHandle, _LocalConnection] = {}
        self._lock = RLock()
        self._provider_lock = RLock()
        self._closed = False

    @property
    def healthy(self) -> bool:
        with self._lock:
            return not self._closed

    @property
    def provider_id(self) -> str:
        return self._provider.provider_id

    @property
    def driver_catalog(self) -> DriverCatalog:
        return self._driver_catalog

    @property
    def payload_catalog(self) -> PayloadCodecCatalog:
        return self._payload_codecs.catalog

    def describe(
        self,
        bindings: tuple[InstrumentBindingSpec, ...],
    ) -> InstrumentProviderDescription:
        with self._provider_lock:
            with self._lock:
                if self._closed:
                    raise InstrumentBackendUnavailable(
                        "instrument backend is shut down"
                    )
            return self._provider.describe(InstrumentProviderContext(bindings=bindings))

    def connect(
        self,
        *,
        binding: InstrumentBindingSpec,
        expected: InstrumentDescription,
    ) -> ConnectedInstrument:
        with self._provider_lock:
            with self._lock:
                if self._closed:
                    raise InstrumentBackendUnavailable(
                        "instrument backend is shut down"
                    )
            driver, actual = self._open_driver(binding, expected=expected)
            handle = InstrumentHandle(
                endpoint_id=self._endpoint_id,
                token=uuid4().hex,
            )
            connection = _LocalConnection(driver=driver, lock=RLock())
            with self._lock:
                if self._closed:
                    with suppress(Exception):
                        driver.disconnect()
                    raise InstrumentBackendUnavailable(
                        "instrument backend is shut down"
                    )
                self._connections[handle] = connection
            return ConnectedInstrument(handle=handle, description=actual)

    def probe(self, binding: InstrumentBindingSpec) -> InstrumentDescription:
        with self._provider_lock:
            with self._lock:
                if self._closed:
                    raise InstrumentBackendUnavailable(
                        "instrument backend is shut down"
                    )
            driver, description = self._open_driver(binding)
            try:
                driver.disconnect()
            except Exception as error:
                raise InstrumentBackendUnavailable(
                    "instrument probe cleanup failed"
                ) from error
            return description

    def _open_driver(
        self,
        binding: InstrumentBindingSpec,
        *,
        expected: InstrumentDescription | None = None,
    ) -> tuple[InstrumentDriver, InstrumentDescription]:
        driver: InstrumentDriver | None = None
        try:
            driver = self._provider.connect(
                InstrumentConnectionContext(binding=binding)
            )
            problems = validate_instruments(
                bindings=(binding,),
                instruments=[driver],
            )
            described, description_problems = describe_instruments([driver])
            problems.extend(description_problems)
            actual = described[0] if described else None
            if expected is not None and actual is not None and actual != expected:
                problems.append(_description_changed_problem(binding.id))
            if problems:
                raise InstrumentBackendRejected(
                    "instrument provider returned an invalid driver",
                    problems=tuple(problems),
                )
            if actual is None:
                raise AssertionError(
                    "validated instrument connection has no description"
                )
            return driver, actual
        except DriverFault as error:
            if driver is not None:
                with suppress(Exception):
                    driver.disconnect()
            raise InstrumentBackendRejected(
                "instrument provider rejected connection",
                problems=(error.problem,),
            ) from error
        except InstrumentBackendRejected:
            if driver is not None:
                with suppress(Exception):
                    driver.disconnect()
            raise
        except Exception as error:
            if driver is not None:
                with suppress(Exception):
                    driver.disconnect()
            raise InstrumentBackendUnavailable(
                "instrument connection could not be established"
            ) from error

    def read_state(
        self,
        handle: InstrumentHandle,
        request: BackendReadRequest,
    ) -> InstrumentStateReadback:
        with self._locked_connection(handle) as connection:
            return project_state_readback(
                connection.driver.instrument_id,
                connection.driver.read_state(lower_state_read_request(request)),
            )

    def apply_state(
        self,
        handle: InstrumentHandle,
        request: BackendApplyRequest,
    ) -> ApplyReceipt:
        with self._locked_connection(handle) as connection:
            return project_apply_outcome(
                connection.driver.instrument_id,
                connection.driver.apply_state(lower_state_patch(request)),
            )

    def invoke(
        self,
        handle: InstrumentHandle,
        request: BackendInvokeRequest,
    ) -> InvokeReceipt:
        with self._locked_connection(handle) as connection:
            try:
                driver_request = decode_driver_operation(
                    request,
                    self._payload_codecs,
                )
            except Exception:
                return InvokeReceipt(
                    status="not_invoked",
                    problems=(_payload_decode_problem(),),
                )
            return project_invoke_outcome(
                connection.driver.instrument_id,
                connection.driver.invoke(driver_request),
            )

    def collect(
        self,
        handle: InstrumentHandle,
        request: BackendCollectRequest,
    ) -> CollectReceipt:
        with self._locked_connection(handle) as connection:
            return project_collect_outcome(
                request,
                connection.driver.collect(lower_acquisition(request)),
            )

    def prepare_acquisitions(
        self,
        handle: InstrumentHandle,
        plan: BackendAcquisitionPlan,
    ) -> AcquisitionPreparationReceipt:
        with self._locked_connection(handle) as connection:
            driver = connection.driver
            if not isinstance(driver, AcquisitionPreparer):
                return AcquisitionPreparationReceipt()
            return project_acquisition_preparation_outcome(
                driver.prepare_acquisitions(lower_acquisition_plan(plan))
            )

    def abort(self, handle: InstrumentHandle) -> None:
        with self._locked_connection(handle) as connection:
            connection.driver.abort()

    def disconnect(self, handle: InstrumentHandle) -> None:
        connection = self._pop_connection(handle)
        with connection.lock:
            connection.driver.disconnect()

    def shutdown(self) -> None:
        with self._provider_lock, self._lock:
            if self._closed:
                return
            self._closed = True
            connections = tuple(reversed(self._connections.values()))
            self._connections.clear()
        errors: list[Exception] = []
        for connection in connections:
            try:
                with connection.lock:
                    connection.driver.disconnect()
            except Exception as error:
                errors.append(error)
        if errors:
            raise ExceptionGroup("instrument backend shutdown failed", errors)

    @contextmanager
    def _locked_connection(
        self,
        handle: InstrumentHandle,
    ) -> Generator[_LocalConnection]:
        with self._lock:
            connection = self._find_connection(handle)
        connection.lock.acquire()
        try:
            with self._lock:
                if self._connections.get(handle) is not connection:
                    raise InstrumentHandleInvalid("instrument handle is stale")
            yield connection
        finally:
            connection.lock.release()

    def _pop_connection(self, handle: InstrumentHandle) -> _LocalConnection:
        with self._lock:
            connection = self._find_connection(handle)
            del self._connections[handle]
            return connection

    def _find_connection(self, handle: InstrumentHandle) -> _LocalConnection:
        if handle.endpoint_id != self._endpoint_id:
            raise InstrumentHandleInvalid(
                "instrument handle belongs to another backend endpoint"
            )
        try:
            return self._connections[handle]
        except KeyError as error:
            raise InstrumentHandleInvalid("instrument handle is stale") from error


def _description_changed_problem(instrument_id: str) -> Problem:
    return problem(
        "instrument_description_changed",
        f"instrument description changed while provisioning {instrument_id}",
        phase=ProblemPhase.PROVIDER_PREFLIGHT,
        location=model_location("instrument_provider", "instruments", instrument_id),
    )


def _payload_decode_problem() -> Problem:
    return problem(
        "instrument_payload_decode_failed",
        "instrument operation payload could not be decoded",
        phase=ProblemPhase.EXECUTION,
        location=model_location("instrument_operation", "arguments"),
    )


__all__ = [
    "ConnectedInstrument",
    "InstrumentBackendEndpoint",
    "InstrumentBackendError",
    "InstrumentBackendRejected",
    "InstrumentBackendUnavailable",
    "InstrumentHandle",
    "InstrumentHandleInvalid",
    "LocalInstrumentBackendEndpoint",
]
