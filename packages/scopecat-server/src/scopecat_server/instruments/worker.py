"""Long-lived subprocess boundary with framed driver payloads and results."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass, field, replace
from multiprocessing import get_context
from multiprocessing.process import BaseProcess
from pathlib import Path
from threading import Event, Lock, RLock, Thread
from time import monotonic
from typing import Annotated, Literal, Never, Protocol
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    model_validator,
)
from scopecat.kernel.problems import Problem
from scopecat.project import load_instrument_backend_factory
from scopecat.records.config import InstrumentBindingSpec
from scopecat.records.instrument import InstrumentStateReadback
from scopecat.sdk.instruments.backend import (
    BackendAcquisitionPlan,
    BackendApplyRequest,
    BackendCollectRequest,
    BackendInvokeRequest,
    BackendReadRequest,
)
from scopecat.sdk.instruments.catalog import DriverCatalog
from scopecat.sdk.instruments.commands import (
    AcquisitionPreparationReceipt,
    ApplyReceipt,
    CollectReceipt,
    InvokeReceipt,
)
from scopecat.sdk.instruments.contracts import InstrumentDescription
from scopecat.sdk.instruments.provider import DriverFault, InstrumentProviderDescription
from scopecat.sdk.payloads import PayloadCodecCatalog

from .backend import (
    ConnectedInstrument,
    InstrumentBackendError,
    InstrumentBackendRejected,
    InstrumentBackendUnavailable,
    InstrumentHandle,
    InstrumentHandleInvalid,
    LocalInstrumentBackendEndpoint,
)
from .worker_output import (
    diagnostic_reference,
    record_exception,
    record_problem,
    worker_operation,
)
from .worker_process import run_instrument_worker
from .worker_wire import (
    DEFAULT_WIRE_LIMITS,
    CollectFrames,
    InvokeFrames,
    WorkerWireError,
    collect_attachment_sizes,
    invoke_attachment_sizes,
    join_collect_receipt,
    join_invoke_request,
    split_collect_receipt,
    split_invoke_request,
)

type _Operation = Literal[
    "describe",
    "probe",
    "connect",
    "read_state",
    "apply_state",
    "invoke",
    "prepare_acquisitions",
    "collect",
    "abort",
    "disconnect",
    "shutdown",
]
type _ErrorKind = Literal[
    "rejected",
    "unavailable",
    "invalid_handle",
    "backend_error",
]
type _NonEmptyText = Annotated[str, Field(min_length=1)]

_CONTROL_VERSION = 1
_MAX_CONTROL_BYTES = 1 * 1024 * 1024
_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])


class _ByteConnection(Protocol):
    def send_bytes(self, buf: bytes | memoryview) -> None: ...

    def recv_bytes(self, maxlength: int | None = None) -> bytes: ...

    def poll(self, timeout: float = 0.0) -> bool: ...

    def close(self) -> None: ...


class _WireModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        allow_inf_nan=False,
    )


class _ChildHandle(_WireModel):
    endpoint_id: _NonEmptyText
    token: _NonEmptyText

    @classmethod
    def from_handle(cls, handle: InstrumentHandle) -> _ChildHandle:
        return cls(endpoint_id=handle.endpoint_id, token=handle.token)

    def to_handle(self) -> InstrumentHandle:
        return InstrumentHandle(endpoint_id=self.endpoint_id, token=self.token)


class _ProviderDescription(_WireModel):
    provider_id: _NonEmptyText
    instruments: tuple[InstrumentDescription, ...] = ()
    problems: tuple[Problem, ...] = ()

    @classmethod
    def from_description(
        cls,
        description: InstrumentProviderDescription,
    ) -> _ProviderDescription:
        return cls(
            provider_id=description.provider_id,
            instruments=description.instruments,
            problems=description.problems,
        )

    def to_description(self) -> InstrumentProviderDescription:
        return InstrumentProviderDescription(
            provider_id=self.provider_id,
            instruments=self.instruments,
            problems=self.problems,
        )


class _RpcRequest(_WireModel):
    protocol_version: Literal[1] = _CONTROL_VERSION
    request_id: int = Field(ge=1)
    operation: _Operation
    handle: _ChildHandle | None = None
    instrument_id: str | None = None
    body: dict[str, JsonValue] | None = None
    attachment_count: int = Field(
        default=0,
        ge=0,
        le=DEFAULT_WIRE_LIMITS.max_attachments,
    )

    @model_validator(mode="after")
    def validate_frames(self) -> _RpcRequest:
        if self.operation != "invoke" and self.attachment_count:
            raise ValueError("only worker invoke requests can have attachments")
        return self


class _RpcError(_WireModel):
    kind: _ErrorKind
    code: _NonEmptyText
    message: _NonEmptyText
    problems: tuple[Problem, ...] = ()
    diagnostic: dict[str, str | int] | None = None


class _RpcResponse(_WireModel):
    protocol_version: Literal[1] = _CONTROL_VERSION
    request_id: int = Field(ge=1)
    status: Literal["ok", "error"]
    body: dict[str, JsonValue] | None = None
    frame_kind: Literal["collect"] | None = None
    attachment_count: int = Field(
        default=0,
        ge=0,
        le=DEFAULT_WIRE_LIMITS.max_attachments,
    )
    error: _RpcError | None = None
    diagnostic: dict[str, str | int] | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> _RpcResponse:
        if (self.status == "error") != (self.error is not None):
            raise ValueError("worker response status and error disagree")
        if self.frame_kind is None:
            if self.attachment_count:
                raise ValueError("unframed worker response cannot have attachments")
        elif self.status != "ok" or self.body is not None:
            raise ValueError("framed worker response must be a bodyless success")
        return self


class _StartupResponse(_WireModel):
    protocol_version: Literal[1] = _CONTROL_VERSION
    status: Literal["ready", "error"]
    worker_pid: int = Field(gt=0)
    provider_id: str | None = None
    driver_catalog: dict[str, JsonValue] | None = None
    payload_catalog: dict[str, JsonValue] | None = None
    error: _RpcError | None = None
    diagnostic: dict[str, str | int] | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> _StartupResponse:
        if self.status == "ready":
            if (
                self.provider_id is None
                or self.driver_catalog is None
                or self.payload_catalog is None
            ):
                raise ValueError("ready worker requires backend metadata")
            if self.error is not None:
                raise ValueError("ready worker cannot include an error")
        elif self.error is None:
            raise ValueError("failed worker startup requires an error")
        return self


@dataclass(frozen=True, slots=True)
class _ReceivedResponse:
    response: _RpcResponse
    collect_receipt: CollectReceipt | None = None


@dataclass(slots=True)
class _PendingResponse:
    event: Event = field(default_factory=Event)
    received: _ReceivedResponse | None = None
    error: InstrumentBackendError | None = None


@dataclass(frozen=True, slots=True)
class _OutgoingResponse:
    response: _RpcResponse
    collect_frames: CollectFrames | None = None


@dataclass(frozen=True, slots=True)
class _ShutdownExchange:
    request: _RpcRequest
    pending: _PendingResponse


class SubprocessInstrumentBackendEndpoint:
    """Serve one project's persistent drivers from one spawned worker."""

    def __init__(
        self,
        project_root: str | Path,
        instrument_backend_spec: str,
        *,
        startup_timeout: float = 10.0,
        operation_timeout: float = 30.0,
        shutdown_timeout: float = 2.0,
    ) -> None:
        if startup_timeout <= 0 or operation_timeout <= 0 or shutdown_timeout <= 0:
            raise ValueError("instrument worker timeouts must be positive")
        self._project_root = Path(project_root).resolve()
        self._operation_timeout = operation_timeout
        self._shutdown_timeout = shutdown_timeout
        self._endpoint_id = uuid4().hex
        self._handles: dict[str, _ChildHandle] = {}
        self._instrument_ids: dict[str, str] = {}
        self._state_lock = RLock()
        self._send_lock = Lock()
        self._shutdown_lock = Lock()
        self._pending: dict[int, _PendingResponse] = {}
        self._next_request_id = 1
        self._closed = False
        self._available = False
        self._connection_closed = False
        self._cleanup_complete = False

        context = get_context("spawn")
        parent, child = context.Pipe(duplex=True)
        process = context.Process(
            target=run_instrument_worker,
            args=(
                child,
                str(self._project_root),
                instrument_backend_spec,
                self._endpoint_id,
            ),
            name=f"scopecat-instruments-{self._project_root.name}",
            daemon=True,
        )
        self._connection: _ByteConnection = parent
        self._process: BaseProcess = process
        try:
            process.start()
        except BaseException:
            parent.close()
            child.close()
            raise
        child.close()

        try:
            if not parent.poll(startup_timeout):
                raise InstrumentBackendUnavailable(
                    "instrument worker did not start in time"
                )
            startup = _recv_model(parent, _StartupResponse)
            if startup.status != "ready":
                assert startup.error is not None
                raise InstrumentBackendUnavailable(
                    startup.error.message, diagnostic=startup.error.diagnostic
                )
            assert startup.provider_id is not None
            assert startup.driver_catalog is not None
            assert startup.payload_catalog is not None
            self._provider_id = startup.provider_id
            self._driver_catalog = _model_from_body(
                DriverCatalog,
                startup.driver_catalog,
            )
            self._payload_catalog = _model_from_body(
                PayloadCodecCatalog,
                startup.payload_catalog,
            )
            self._worker_pid = startup.worker_pid
            self._diagnostic = startup.diagnostic
            self._available = True
            self._receiver = Thread(
                target=self._receive_responses,
                name=f"scopecat-instruments-receiver-{self._worker_pid}",
                daemon=True,
            )
            self._receiver.start()
        except BaseException:
            parent.close()
            _stop_process(process, shutdown_timeout)
            raise

    @property
    def healthy(self) -> bool:
        with self._state_lock:
            if self._closed or not self._available:
                return False
            if not self._process.is_alive():
                self._mark_unavailable()
                return False
            return True

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def driver_catalog(self) -> DriverCatalog:
        return self._driver_catalog

    @property
    def payload_catalog(self) -> PayloadCodecCatalog:
        return self._payload_catalog

    @property
    def worker_pid(self) -> int:
        return self._worker_pid

    def describe(
        self,
        bindings: tuple[InstrumentBindingSpec, ...],
    ) -> InstrumentProviderDescription:
        received = self._rpc(
            "describe",
            body={
                "bindings": [_model_to_body(binding) for binding in bindings],
            },
        )
        return self._decode_response(
            _ProviderDescription,
            received.response,
        ).to_description()

    def connect(
        self,
        *,
        binding: InstrumentBindingSpec,
        expected: InstrumentDescription,
    ) -> ConnectedInstrument:
        received = self._rpc(
            "connect",
            body={
                "binding": _model_to_body(binding),
                "expected": _model_to_body(expected),
            },
        )
        try:
            body = _require_response_body(received.response)
            child_handle = _model_from_body(
                _ChildHandle,
                _require_mapping(body, "handle"),
            )
            description = _model_from_body(
                InstrumentDescription,
                _require_mapping(body, "description"),
            )
        except (InstrumentBackendUnavailable, ValueError) as error:
            self._raise_invalid_response(error)
        token = uuid4().hex
        with self._state_lock:
            self._require_available()
            self._handles[token] = child_handle
            self._instrument_ids[token] = binding.id
        return ConnectedInstrument(
            handle=InstrumentHandle(endpoint_id=self._endpoint_id, token=token),
            description=description,
        )

    def probe(self, binding: InstrumentBindingSpec) -> InstrumentDescription:
        received = self._rpc(
            "probe",
            body={"binding": _model_to_body(binding)},
        )
        return self._decode_response(
            InstrumentDescription,
            received.response,
        )

    def read_state(
        self,
        handle: InstrumentHandle,
        request: BackendReadRequest,
    ) -> InstrumentStateReadback:
        received = self._rpc(
            "read_state",
            handle=handle,
            body={"request": _model_to_body(request)},
        )
        return self._decode_response(
            InstrumentStateReadback,
            received.response,
        )

    def apply_state(
        self,
        handle: InstrumentHandle,
        request: BackendApplyRequest,
    ) -> ApplyReceipt:
        received = self._rpc(
            "apply_state",
            handle=handle,
            body={"request": _model_to_body(request)},
        )
        return self._decode_response(ApplyReceipt, received.response)

    def invoke(
        self,
        handle: InstrumentHandle,
        request: BackendInvokeRequest,
    ) -> InvokeReceipt:
        frames = split_invoke_request(request)
        received = self._rpc(
            "invoke",
            handle=handle,
            frames=frames,
        )
        return self._decode_response(InvokeReceipt, received.response)

    def collect(
        self,
        handle: InstrumentHandle,
        request: BackendCollectRequest,
    ) -> CollectReceipt:
        received = self._rpc(
            "collect",
            handle=handle,
            body={"request": _model_to_body(request)},
        )
        if received.collect_receipt is None:
            self._raise_invalid_response(
                ValueError("instrument worker omitted its collect receipt frames")
            )
        return received.collect_receipt

    def prepare_acquisitions(
        self,
        handle: InstrumentHandle,
        plan: BackendAcquisitionPlan,
    ) -> AcquisitionPreparationReceipt:
        received = self._rpc(
            "prepare_acquisitions",
            handle=handle,
            body={"plan": _model_to_body(plan)},
        )
        return self._decode_response(
            AcquisitionPreparationReceipt,
            received.response,
        )

    def abort(self, handle: InstrumentHandle) -> None:
        self._rpc("abort", handle=handle)

    def disconnect(self, handle: InstrumentHandle) -> None:
        self._rpc("disconnect", handle=handle)
        with self._state_lock:
            self._handles.pop(handle.token, None)
            self._instrument_ids.pop(handle.token, None)

    def shutdown(self) -> None:
        with self._shutdown_lock:
            deadline = monotonic() + self._shutdown_timeout
            with self._state_lock:
                if self._cleanup_complete:
                    return
                exchange = self._begin_shutdown_locked()
            if exchange is not None and not self._send_shutdown(exchange, deadline):
                with self._state_lock:
                    self._pending.pop(exchange.request.request_id, None)
                exchange = None
            response = self._stop_generation(
                deadline=deadline,
                graceful=exchange,
            )
            if response is not None and response.status == "error":
                assert response.error is not None
                _raise_worker_error(response.error)

    def _begin_shutdown_locked(self) -> _ShutdownExchange | None:
        if (
            self._closed
            or not self._available
            or self._connection_closed
            or not self._process.is_alive()
        ):
            self._closed = True
            self._fence_generation_locked(
                InstrumentBackendUnavailable("instrument worker is shut down")
            )
            return None
        request = self._new_request("shutdown")
        pending = _PendingResponse()
        self._pending[request.request_id] = pending
        self._closed = True
        self._fence_generation_locked(
            InstrumentBackendUnavailable("instrument worker is shut down"),
            preserve_request_id=request.request_id,
        )
        return _ShutdownExchange(request=request, pending=pending)

    def _send_shutdown(
        self,
        exchange: _ShutdownExchange,
        deadline: float,
    ) -> bool:
        """Request child cleanup without closing its receiver-owned Pipe."""

        acquired = self._send_lock.acquire(timeout=max(0.0, deadline - monotonic()))
        if not acquired:
            return False
        try:
            _send_model(self._connection, exchange.request)
        except EOFError, OSError, ValueError:
            return False
        finally:
            self._send_lock.release()
        return True

    def _rpc(
        self,
        operation: _Operation,
        *,
        handle: InstrumentHandle | None = None,
        body: dict[str, JsonValue] | None = None,
        frames: InvokeFrames | None = None,
    ) -> _ReceivedResponse:
        with self._state_lock:
            self._require_available()
            child_handle = None if handle is None else self._resolve_handle(handle)
            request = self._new_request(
                operation,
                handle=child_handle,
                instrument_id=None
                if handle is None
                else self._instrument_ids[handle.token],
                body=body,
                attachment_count=0 if frames is None else len(frames.attachments),
            )
            pending = _PendingResponse()
            self._pending[request.request_id] = pending
        try:
            with self._send_lock:
                self._require_available()
                _send_model(self._connection, request)
                if frames is not None:
                    self._connection.send_bytes(frames.header)
                    for attachment in frames.attachments:
                        self._connection.send_bytes(attachment)
        except InstrumentBackendUnavailable:
            with self._state_lock:
                self._pending.pop(request.request_id, None)
            raise
        except (EOFError, OSError, ValueError) as error:
            self._mark_unavailable()
            raise InstrumentBackendUnavailable(
                "instrument worker is unavailable"
            ) from error

        if not pending.event.wait(self._operation_timeout):
            timed_out = self._stop_timed_out_generation(
                request.request_id,
                pending,
                operation=operation,
            )
            if not timed_out:
                pending.event.wait()
        if pending.error is not None:
            reference = self._request_diagnostic(request)
            raise InstrumentBackendUnavailable(
                str(pending.error),
                diagnostic=reference,
                problems=pending.error.problems,
            ) from pending.error
        received = pending.received
        if received is None:
            raise InstrumentBackendUnavailable("instrument worker returned no response")
        response = received.response
        if response.status == "error":
            assert response.error is not None
            _raise_worker_error(response.error)
        return received

    def _request_diagnostic(self, request: _RpcRequest) -> dict[str, str | int] | None:
        if self._diagnostic is None:
            return None
        reference = {
            **self._diagnostic,
            "request_id": request.request_id,
            "operation": request.operation,
        }
        if request.instrument_id is not None:
            reference["instrument_id"] = request.instrument_id
        return reference

    def _receive_responses(self) -> None:
        while True:
            try:
                response = _recv_model(self._connection, _RpcResponse)
                collect_receipt = (
                    _receive_collect(
                        self._connection,
                        response.attachment_count,
                    )
                    if response.frame_kind == "collect"
                    else None
                )
            except EOFError, OSError, ValueError:
                with self._state_lock:
                    if self._closed:
                        return
                self._mark_unavailable()
                return
            with self._state_lock:
                pending = self._pending.get(response.request_id)
                closed = self._closed
                if pending is not None:
                    pending.received = _ReceivedResponse(
                        response=response,
                        collect_receipt=collect_receipt,
                    )
                    pending.event.set()
                    self._pending.pop(response.request_id, None)
            if pending is None:
                if closed:
                    continue
                self._mark_unavailable()
                return

    def _decode_response[ModelT: BaseModel](
        self,
        model_type: type[ModelT],
        response: _RpcResponse,
    ) -> ModelT:
        try:
            return _model_from_body(
                model_type,
                _require_response_body(response),
            )
        except (InstrumentBackendUnavailable, ValueError) as error:
            self._raise_invalid_response(error)

    def _raise_invalid_response(self, error: Exception) -> Never:
        self._mark_unavailable()
        raise InstrumentBackendUnavailable(
            "instrument worker returned an invalid response"
        ) from error

    def _new_request(
        self,
        operation: _Operation,
        *,
        handle: _ChildHandle | None = None,
        instrument_id: str | None = None,
        body: dict[str, JsonValue] | None = None,
        attachment_count: int = 0,
    ) -> _RpcRequest:
        request = _RpcRequest(
            request_id=self._next_request_id,
            operation=operation,
            handle=handle,
            instrument_id=instrument_id,
            body=body,
            attachment_count=attachment_count,
        )
        self._next_request_id += 1
        return request

    def _resolve_handle(self, handle: InstrumentHandle) -> _ChildHandle:
        with self._state_lock:
            if handle.endpoint_id != self._endpoint_id:
                raise InstrumentHandleInvalid(
                    "instrument handle belongs to another endpoint generation"
                )
            try:
                return self._handles[handle.token]
            except KeyError as error:
                raise InstrumentHandleInvalid("instrument handle is stale") from error

    def _require_available(self) -> None:
        with self._state_lock:
            if self._closed:
                raise InstrumentBackendUnavailable("instrument worker is shut down")
            if not self._available or not self._process.is_alive():
                self._mark_unavailable()
                raise InstrumentBackendUnavailable("instrument worker is unavailable")

    def _mark_unavailable(self) -> None:
        with self._state_lock:
            if not self._available and not self._pending:
                return
            self._fence_generation_locked(
                InstrumentBackendUnavailable("instrument worker is unavailable")
            )
        self._close_connection()

    def _stop_timed_out_generation(
        self,
        request_id: int,
        pending: _PendingResponse,
        *,
        operation: _Operation,
    ) -> bool:
        """Terminate a generation whose running driver call cannot be cancelled."""

        with self._shutdown_lock:
            with self._state_lock:
                if self._pending.get(request_id) is not pending:
                    return False
                self._closed = True
                self._fence_generation_locked(
                    InstrumentBackendUnavailable(
                        f"instrument worker {operation} timed out"
                    )
                )
            self._stop_generation()
            return True

    def _fence_generation_locked(
        self,
        error: InstrumentBackendUnavailable,
        *,
        preserve_request_id: int | None = None,
    ) -> None:
        self._available = False
        self._handles.clear()
        self._instrument_ids.clear()
        for request_id, item in tuple(self._pending.items()):
            if request_id == preserve_request_id:
                continue
            item.error = error
            item.event.set()
            self._pending.pop(request_id)

    def _stop_generation(
        self,
        *,
        deadline: float | None = None,
        graceful: _ShutdownExchange | None = None,
    ) -> _RpcResponse | None:
        selected_deadline = deadline or (monotonic() + self._shutdown_timeout)
        response: _RpcResponse | None = None
        process_closed = False
        try:
            if graceful is not None:
                graceful.pending.event.wait(
                    max(0.0, (selected_deadline - monotonic()) / 2)
                )
                received = graceful.pending.received
                if received is not None:
                    response = received.response
                    self._process.join(max(0.0, (selected_deadline - monotonic()) / 2))
            self._close_connection()
            _terminate_process_until(self._process, selected_deadline)
            self._receiver.join(max(0.0, selected_deadline - monotonic()))
            if self._receiver.is_alive():
                raise InstrumentBackendUnavailable(
                    "instrument worker receiver did not stop"
                )
            self._process.close()
            process_closed = True
            return response
        finally:
            with self._state_lock:
                if graceful is not None:
                    self._pending.pop(
                        graceful.request.request_id,
                        None,
                    )
                self._cleanup_complete = process_closed

    def _close_connection(self) -> None:
        with self._state_lock:
            if self._connection_closed:
                return
            self._connection_closed = True
        with suppress(Exception):
            self._connection.close()


def _instrument_worker_main(
    connection: _ByteConnection,
    project_root: str,
    instrument_backend_spec: str,
) -> None:
    endpoint: LocalInstrumentBackendEndpoint | None = None
    executor: ThreadPoolExecutor | None = None
    shutdown_request: _RpcRequest | None = None
    try:
        try:
            create_backend = load_instrument_backend_factory(
                instrument_backend_spec,
                project_root,
            )
            endpoint = LocalInstrumentBackendEndpoint(
                create_backend(Path(project_root))
            )
            _send_model(
                connection,
                _StartupResponse(
                    status="ready",
                    worker_pid=os.getpid(),
                    diagnostic=diagnostic_reference({"operation": "startup"}),
                    provider_id=endpoint.provider_id,
                    driver_catalog=_model_to_body(endpoint.driver_catalog),
                    payload_catalog=_model_to_body(endpoint.payload_catalog),
                ),
            )
        except Exception as error:
            record_exception(error)
            with suppress(Exception):
                _send_model(
                    connection,
                    _StartupResponse(
                        status="error",
                        worker_pid=os.getpid(),
                        error=_RpcError(
                            kind="unavailable",
                            code="instrument_worker_start_failed",
                            message="instrument worker failed to start",
                            diagnostic=diagnostic_reference({"operation": "startup"}),
                        ),
                    ),
                )
            return

        executor = ThreadPoolExecutor(
            max_workers=32,
            thread_name_prefix="scopecat-instrument-rpc",
        )
        response_lock = Lock()
        while True:
            try:
                request = _recv_model(connection, _RpcRequest)
            except EOFError, OSError, ValueError:
                return
            if request.operation == "shutdown":
                shutdown_request = request
                break
            try:
                invoke_request = (
                    _receive_invoke(connection, request.attachment_count)
                    if request.operation == "invoke"
                    else None
                )
            except Exception:
                return
            executor.submit(
                _dispatch_and_respond,
                connection,
                response_lock,
                endpoint,
                request,
                invoke_request,
            )
    finally:
        cleanup_errors: list[Exception] = []
        if executor is not None:
            try:
                executor.shutdown(wait=True, cancel_futures=False)
            except Exception as error:
                record_exception(error)
                cleanup_errors.append(error)
        if endpoint is not None:
            try:
                endpoint.shutdown()
            except Exception as error:
                record_exception(error)
                cleanup_errors.append(error)
        if shutdown_request is not None:
            response = (
                _RpcResponse(
                    request_id=shutdown_request.request_id,
                    status="error",
                    error=_RpcError(
                        kind="backend_error",
                        code="instrument_worker_shutdown_failed",
                        message="instrument worker cleanup failed",
                        diagnostic=diagnostic_reference(
                            {
                                "request_id": shutdown_request.request_id,
                                "operation": "shutdown",
                            }
                        ),
                    ),
                )
                if cleanup_errors
                else _RpcResponse(
                    request_id=shutdown_request.request_id,
                    status="ok",
                )
            )
            with suppress(Exception):
                _send_model(connection, response)
        connection.close()


def _dispatch_and_respond(
    connection: _ByteConnection,
    response_lock: Lock,
    endpoint: LocalInstrumentBackendEndpoint,
    request: _RpcRequest,
    invoke_request: BackendInvokeRequest | None,
) -> None:
    context: dict[str, str | int] = {
        "request_id": request.request_id,
        "operation": request.operation,
    }
    if invoke_request is not None:
        context["operation"] = f"invoke:{invoke_request.operation_id}"
    if request.instrument_id is not None:
        context["instrument_id"] = request.instrument_id
    elif request.body is not None:
        binding = request.body.get("binding")
        if isinstance(binding, dict):
            instrument_id = binding.get("id")
            if isinstance(instrument_id, str):
                context["instrument_id"] = instrument_id
    with worker_operation(context):
        try:
            outgoing = _dispatch_request(
                endpoint,
                request,
                invoke_request=invoke_request,
            )
        except Exception as error:
            record_exception(error)
            response = _error_response(request.request_id, error)
            assert response.error is not None
            outgoing = _OutgoingResponse(
                response=response.model_copy(
                    update={
                        "error": response.error.model_copy(
                            update={"diagnostic": diagnostic_reference(context)}
                        )
                    }
                )
            )
        reference = diagnostic_reference(context)
        if reference is not None:
            outgoing = _with_diagnostic(outgoing, reference)
    try:
        with response_lock:
            _send_model(connection, outgoing.response)
            frames = outgoing.collect_frames
            if frames is not None:
                connection.send_bytes(frames.header)
                for attachment in frames.attachments:
                    connection.send_bytes(attachment)
    except EOFError, OSError, ValueError:
        with suppress(OSError):
            connection.close()


def _annotate_problems(
    problems: tuple[Problem, ...], reference: dict[str, str | int]
) -> tuple[Problem, ...]:
    for item in problems:
        record_problem(item.code, item.message)
    return tuple(
        item.model_copy(
            update={
                "message": item.message[:512],
                "details": {**item.details, "worker_diagnostic": reference},
            }
        )
        for item in problems
    )


def _with_diagnostic(
    outgoing: _OutgoingResponse, reference: dict[str, str | int]
) -> _OutgoingResponse:
    body = outgoing.response.body
    if body is not None:
        problems = body.get("problems")
        if isinstance(problems, list):
            body = {
                **body,
                "problems": [
                    item.model_dump(mode="json")
                    for item in _annotate_problems(
                        tuple(Problem.model_validate(item) for item in problems),
                        reference,
                    )
                ],
            }
    error = outgoing.response.error
    if error is not None:
        error = error.model_copy(
            update={
                "problems": _annotate_problems(error.problems, reference),
                "diagnostic": reference,
            }
        )
    return replace(
        outgoing,
        response=outgoing.response.model_copy(
            update={"body": body, "diagnostic": reference, "error": error}
        ),
    )


def _dispatch_request(
    endpoint: LocalInstrumentBackendEndpoint,
    request: _RpcRequest,
    *,
    invoke_request: BackendInvokeRequest | None,
) -> _OutgoingResponse:
    operation = request.operation
    body = request.body or {}
    if operation == "describe":
        description = endpoint.describe(_bindings_from_body(body))
        return _OutgoingResponse(
            response=_ok_response(
                request,
                _ProviderDescription.from_description(description),
            )
        )
    if operation == "probe":
        description = endpoint.probe(
            _model_from_body(
                InstrumentBindingSpec,
                _require_mapping(body, "binding"),
            )
        )
        return _OutgoingResponse(response=_ok_response(request, description))
    if operation == "connect":
        connection = endpoint.connect(
            binding=_model_from_body(
                InstrumentBindingSpec,
                _require_mapping(body, "binding"),
            ),
            expected=_model_from_body(
                InstrumentDescription,
                _require_mapping(body, "expected"),
            ),
        )
        return _OutgoingResponse(
            response=_RpcResponse(
                request_id=request.request_id,
                status="ok",
                body={
                    "handle": _model_to_body(
                        _ChildHandle.from_handle(connection.handle)
                    ),
                    "description": _model_to_body(connection.description),
                },
            )
        )

    handle = _require_child_handle(request)
    if operation == "read_state":
        read_request = _model_from_body(
            BackendReadRequest,
            _require_mapping(body, "request"),
        )
        return _OutgoingResponse(
            response=_ok_response(request, endpoint.read_state(handle, read_request))
        )
    if operation == "apply_state":
        receipt = endpoint.apply_state(
            handle,
            _model_from_body(
                BackendApplyRequest,
                _require_mapping(body, "request"),
            ),
        )
        return _OutgoingResponse(response=_ok_response(request, receipt))
    if operation == "invoke":
        if invoke_request is None:
            raise ValueError("invoke request frames are missing")
        return _OutgoingResponse(
            response=_ok_response(
                request,
                endpoint.invoke(handle, invoke_request),
            )
        )
    if operation == "collect":
        receipt = endpoint.collect(
            handle,
            _model_from_body(
                BackendCollectRequest,
                _require_mapping(body, "request"),
            ),
        )
        reference = diagnostic_reference()
        if reference is not None and receipt.problems:
            receipt = receipt.model_copy(
                update={"problems": _annotate_problems(receipt.problems, reference)}
            )
        frames = split_collect_receipt(receipt)
        return _OutgoingResponse(
            response=_RpcResponse(
                request_id=request.request_id,
                status="ok",
                frame_kind="collect",
                attachment_count=len(frames.attachments),
            ),
            collect_frames=frames,
        )
    if operation == "prepare_acquisitions":
        receipt = endpoint.prepare_acquisitions(
            handle,
            _model_from_body(
                BackendAcquisitionPlan,
                _require_mapping(body, "plan"),
            ),
        )
        return _OutgoingResponse(response=_ok_response(request, receipt))
    if operation == "abort":
        endpoint.abort(handle)
        return _OutgoingResponse(
            response=_RpcResponse(request_id=request.request_id, status="ok")
        )
    if operation == "disconnect":
        endpoint.disconnect(handle)
        return _OutgoingResponse(
            response=_RpcResponse(request_id=request.request_id, status="ok")
        )
    raise AssertionError(f"unhandled worker operation: {operation}")


def _ok_response(
    request: _RpcRequest,
    model: BaseModel,
) -> _RpcResponse:
    return _RpcResponse(
        request_id=request.request_id,
        status="ok",
        body=_model_to_body(model),
    )


def _error_response(request_id: int, error: BaseException) -> _RpcResponse:
    if isinstance(error, DriverFault):
        payload = _RpcError(
            kind="backend_error",
            code=error.problem.code,
            message=error.problem.message[:512],
            problems=(error.problem,),
        )
    elif isinstance(error, InstrumentBackendRejected):
        payload = _RpcError(
            kind="rejected",
            code="instrument_backend_rejected",
            message=str(error)[:512],
            problems=error.problems,
        )
    elif isinstance(error, InstrumentHandleInvalid):
        payload = _RpcError(
            kind="invalid_handle",
            code="instrument_handle_invalid",
            message=str(error)[:512],
        )
    elif isinstance(error, InstrumentBackendUnavailable):
        payload = _RpcError(
            kind="unavailable",
            code="instrument_backend_unavailable",
            message=str(error)[:512],
        )
    elif isinstance(error, InstrumentBackendError):
        payload = _RpcError(
            kind="backend_error",
            code="instrument_backend_error",
            message=str(error)[:512],
        )
    else:
        payload = _RpcError(
            kind="backend_error",
            code="instrument_worker_request_failed",
            message="instrument worker request failed",
        )
    return _RpcResponse(
        request_id=request_id,
        status="error",
        error=payload,
    )


def _raise_worker_error(error: _RpcError) -> None:
    if error.kind == "rejected":
        raise InstrumentBackendRejected(
            error.message,
            problems=_annotate_problems(error.problems, error.diagnostic)
            if error.diagnostic
            else error.problems,
            diagnostic=error.diagnostic,
        )
    if error.kind == "invalid_handle":
        raise InstrumentHandleInvalid(error.message, diagnostic=error.diagnostic)
    if error.kind == "unavailable":
        raise InstrumentBackendUnavailable(
            error.message, diagnostic=error.diagnostic, problems=error.problems
        )
    raise InstrumentBackendError(
        error.message, diagnostic=error.diagnostic, problems=error.problems
    )


def _receive_invoke(
    connection: _ByteConnection,
    attachment_count: int,
) -> BackendInvokeRequest:
    header = connection.recv_bytes(DEFAULT_WIRE_LIMITS.max_header_bytes)
    attachments = _receive_attachments(
        connection,
        attachment_count=attachment_count,
        declared_sizes=invoke_attachment_sizes(header),
    )
    return join_invoke_request(header, attachments)


def _receive_collect(
    connection: _ByteConnection,
    attachment_count: int,
) -> CollectReceipt:
    header = connection.recv_bytes(DEFAULT_WIRE_LIMITS.max_header_bytes)
    attachments = _receive_attachments(
        connection,
        attachment_count=attachment_count,
        declared_sizes=collect_attachment_sizes(header),
    )
    return join_collect_receipt(header, attachments)


def _receive_attachments(
    connection: _ByteConnection,
    *,
    attachment_count: int,
    declared_sizes: tuple[int, ...],
) -> tuple[bytes, ...]:
    if attachment_count != len(declared_sizes):
        raise WorkerWireError("worker frame count does not match its manifest")
    attachments: list[bytes] = []
    for declared_size in declared_sizes:
        content = connection.recv_bytes(DEFAULT_WIRE_LIMITS.max_attachment_bytes)
        if len(content) != declared_size:
            raise WorkerWireError("worker frame size does not match its manifest")
        attachments.append(content)
    return tuple(attachments)


def _send_model(connection: _ByteConnection, model: BaseModel) -> None:
    content = model.model_dump_json().encode("utf-8")
    if len(content) > _MAX_CONTROL_BYTES:
        raise ValueError("instrument worker control message exceeds its size limit")
    connection.send_bytes(content)


def _recv_model[ModelT: BaseModel](
    connection: _ByteConnection,
    model_type: type[ModelT],
) -> ModelT:
    return model_type.model_validate_json(connection.recv_bytes(_MAX_CONTROL_BYTES))


def _model_from_body[ModelT: BaseModel](
    model_type: type[ModelT],
    body: dict[str, JsonValue],
) -> ModelT:
    return model_type.model_validate_json(
        json.dumps(
            body,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    )


def _model_to_body(model: BaseModel) -> dict[str, JsonValue]:
    return _JSON_OBJECT.validate_python(model.model_dump(mode="json"))


def _require_response_body(response: _RpcResponse) -> dict[str, JsonValue]:
    if response.body is None:
        raise InstrumentBackendUnavailable(
            "instrument worker returned an incomplete response"
        )
    return response.body


def _require_mapping(
    body: dict[str, JsonValue],
    field: str,
) -> dict[str, JsonValue]:
    value = body.get(field)
    if not isinstance(value, dict):
        raise ValueError(f"worker request requires object field {field!r}")
    return value


def _bindings_from_body(
    body: dict[str, JsonValue],
) -> tuple[InstrumentBindingSpec, ...]:
    value = body.get("bindings")
    if not isinstance(value, list):
        raise ValueError("worker request requires array field 'bindings'")
    bindings: list[InstrumentBindingSpec] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("worker bindings must be objects")
        bindings.append(
            _model_from_body(
                InstrumentBindingSpec,
                item,
            )
        )
    return tuple(bindings)


def _require_child_handle(request: _RpcRequest) -> InstrumentHandle:
    if request.handle is None:
        raise ValueError("worker request requires an instrument handle")
    return request.handle.to_handle()


def _stop_process(process: BaseProcess, timeout: float) -> None:
    _stop_process_until(process, monotonic() + timeout)
    process.close()


def _stop_process_until(process: BaseProcess, deadline: float) -> None:
    remaining = max(0.0, deadline - monotonic())
    process.join(remaining / 2)
    _terminate_process_until(process, deadline)


def _terminate_process_until(process: BaseProcess, deadline: float) -> None:
    if process.is_alive():
        process.terminate()
        remaining = max(0.0, deadline - monotonic())
        process.join(remaining / 2)
    if process.is_alive():
        process.kill()
    process.join(max(0.0, deadline - monotonic()))
    if process.is_alive():
        raise InstrumentBackendUnavailable("instrument worker did not stop")


__all__ = ["SubprocessInstrumentBackendEndpoint"]
