"""Process-local ownership boundary for persistent instrument connections."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass, field
from hashlib import sha256
from threading import Condition, RLock
from typing import Literal, Self

from scopecat.records.instrument import (
    InstrumentStateCacheEntry,
    InstrumentStateCacheReadback,
    InstrumentStateCacheReason,
    InstrumentStateCacheStatus,
    InstrumentStateReadback,
    InstrumentStateSnapshot,
    StateMemberIdentity,
    StateMemberTarget,
    state_member_identity,
    state_member_target,
)
from scopecat.sdk.instruments.backend import (
    BackendAcquisitionPlan,
    BackendApplyRequest,
    BackendCollectRequest,
    BackendInvokeRequest,
    BackendReadRequest,
)
from scopecat.sdk.instruments.commands import (
    AcquisitionPreparationReceipt,
    ApplyReceipt,
    CollectReceipt,
    InvokeReceipt,
)
from scopecat.sdk.instruments.contracts import (
    InstrumentDescription,
    operation_invalidated_state_members,
)

from .backend import (
    ConnectedInstrument,
    InstrumentBackendEndpoint,
    InstrumentHandle,
)


class InstrumentActorError(RuntimeError):
    """Base error for invalid actor lifecycle transitions."""


class InstrumentActorConflict(InstrumentActorError):
    """The requested transition conflicts with current instrument ownership."""


class InstrumentActorShutdown(InstrumentActorError):
    """The registry or actor no longer accepts work."""


@dataclass(frozen=True, slots=True)
class InstrumentBindingKey:
    """Identify the provider binding and contract behind one actor connection."""

    provider_id: str
    binding_fingerprint: str
    contract_fingerprint: str


@dataclass(frozen=True, slots=True)
class InstrumentOwnerKey:
    """Identify one durable control-plane owner and its optional fence."""

    kind: Literal["run", "instrument_session"]
    owner_id: str
    fence: str | None = None


type InstrumentConnector = Callable[[], ConnectedInstrument]
type InstrumentConnection = tuple[InstrumentBackendEndpoint, InstrumentHandle]
type _DefaultCacheStatus = Literal["unobserved", "unknown"]
type _DefaultCacheReason = Literal[
    "not_observed",
    "collect_outcome_unknown",
    "explicit_invalidation",
    "aborted",
]


@dataclass(slots=True)
class _InstrumentStateCache:
    instrument_id: str
    generation: int = 0
    has_baseline: bool = False
    default_status: _DefaultCacheStatus = "unobserved"
    default_generation: int = 0
    default_reason: _DefaultCacheReason = "not_observed"
    entries: dict[StateMemberIdentity, InstrumentStateCacheEntry] = field(
        default_factory=dict
    )

    def snapshot(self) -> InstrumentStateSnapshot | None:
        if not self.has_baseline:
            return None
        observations = [
            entry.observation.model_copy(deep=True)
            for _, entry in sorted(self.entries.items(), key=lambda item: repr(item[0]))
            if entry.status == "observed" and entry.observation is not None
        ]
        return InstrumentStateSnapshot(
            instrument_id=self.instrument_id,
            observations=observations,
        )

    def read(
        self,
        targets: Iterable[StateMemberTarget],
    ) -> InstrumentStateCacheReadback:
        entries: list[InstrumentStateCacheEntry] = []
        for target in targets:
            cached = self.entries.get(state_member_identity(target))
            entries.append(
                InstrumentStateCacheEntry(
                    target=target,
                    status=self.default_status,
                    generation=self.default_generation,
                    reason=self.default_reason,
                )
                if cached is None
                else cached.model_copy(deep=True)
            )
        return InstrumentStateCacheReadback(
            instrument_id=self.instrument_id,
            generation=self.generation,
            entries=entries,
        )

    def replace(self, state: InstrumentStateSnapshot) -> None:
        self.instrument_id = state.instrument_id
        self.generation += 1
        self.has_baseline = True
        self.default_status = "unobserved"
        self.default_generation = 0
        self.default_reason = "not_observed"
        self.entries = {
            state_member_identity(observation.target): InstrumentStateCacheEntry(
                target=observation.target,
                status="observed",
                generation=self.generation,
                observation=observation.model_copy(deep=True),
            )
            for observation in state.observations
        }

    def merge(self, readback: InstrumentStateReadback) -> None:
        self.instrument_id = readback.instrument_id
        self.generation += 1
        self.has_baseline = True
        for observation in readback.observations:
            self.entries[state_member_identity(observation.target)] = (
                InstrumentStateCacheEntry(
                    target=observation.target,
                    status="observed",
                    generation=self.generation,
                    observation=observation.model_copy(deep=True),
                )
            )

    def mark(
        self,
        targets: Iterable[StateMemberTarget],
        *,
        status: InstrumentStateCacheStatus,
        reason: InstrumentStateCacheReason,
    ) -> None:
        selected = tuple(targets)
        if not selected:
            return
        self.generation += 1
        for target in selected:
            self.entries[state_member_identity(target)] = InstrumentStateCacheEntry(
                target=target,
                status=status,
                generation=self.generation,
                reason=reason,
            )

    def mark_all_unknown(self, reason: _DefaultCacheReason) -> None:
        self.generation += 1
        self.entries = {
            identity: InstrumentStateCacheEntry(
                target=entry.target,
                status="unknown",
                generation=self.generation,
                reason=reason,
            )
            for identity, entry in self.entries.items()
        }
        self.has_baseline = False
        self.default_status = "unknown"
        self.default_generation = self.generation
        self.default_reason = reason


class OwnedInstrument:
    """One epoch-fenced view of an actor for a run or direct session."""

    __slots__ = (
        "_actor",
        "_binding",
        "_description",
        "_epoch",
        "_instrument_id",
        "_owner",
        "_reused_connection",
        "connection_context",
        "connection_generation",
    )

    def __init__(
        self,
        actor: _InstrumentActor,
        *,
        instrument_id: str,
        binding: InstrumentBindingKey,
        owner: InstrumentOwnerKey,
        epoch: int,
        description: InstrumentDescription,
        reused_connection: bool,
        connection_generation: str,
        connection_context: Literal["cold", "warm", "reconnect"],
    ) -> None:
        self._actor = actor
        self._instrument_id = instrument_id
        self._binding = binding
        self._owner = owner
        self._epoch = epoch
        self._description = description
        self._reused_connection = reused_connection
        self.connection_generation = connection_generation
        self.connection_context: Literal["cold", "warm", "reconnect"] = (
            connection_context
        )

    @property
    def instrument_id(self) -> str:
        return self._instrument_id

    @property
    def binding(self) -> InstrumentBindingKey:
        return self._binding

    @property
    def owner(self) -> InstrumentOwnerKey:
        return self._owner

    @property
    def epoch(self) -> int:
        return self._epoch

    @property
    def description(self) -> InstrumentDescription:
        return self._description

    @property
    def reused_connection(self) -> bool:
        return self._reused_connection

    @property
    def assumed_state(self) -> InstrumentStateSnapshot | None:
        return self._actor.assumed_state(self)

    def state_cache(
        self,
        targets: Iterable[StateMemberTarget],
    ) -> InstrumentStateCacheReadback:
        """Return exact member knowledge without hardware I/O."""

        return self._actor.state_cache(self, targets)

    def read_state(self, request: BackendReadRequest) -> InstrumentStateReadback:
        """Read hardware under the actor lock without trusting it before validation."""

        return self._actor.read_state(self, request)

    def adopt_readback(self, readback: InstrumentStateReadback) -> None:
        """Merge a caller-validated member readback into the working cache."""

        self._actor.adopt_readback(self, readback)

    def adopt_state(self, state: InstrumentStateSnapshot) -> None:
        """Publish a caller-validated snapshot as this owner's working baseline."""

        self._actor.adopt_state(self, state)

    def invalidate_state(self) -> None:
        self._actor.invalidate_state(self)

    def apply_state(self, request: BackendApplyRequest) -> ApplyReceipt:
        return self._actor.apply_state(self, request)

    def invoke(self, request: BackendInvokeRequest) -> InvokeReceipt:
        return self._actor.invoke(self, request)

    def collect(self, request: BackendCollectRequest) -> CollectReceipt:
        return self._actor.collect(self, request)

    def prepare_acquisitions(
        self,
        plan: BackendAcquisitionPlan,
    ) -> AcquisitionPreparationReceipt:
        return self._actor.prepare_acquisitions(self, plan)

    def abort(self) -> None:
        """Stop owner-scoped hardware work before release or fault."""

        self._actor.abort(self)

    def release(self) -> None:
        """Release ownership while leaving a matching connection available."""

        self._actor.release(self)

    def fault(self) -> None:
        """Invalidate this epoch and discard its possibly desynchronized connection."""

        self._actor.fault(self)


class _InstrumentActor:
    """Serialize one physical instrument without treating idle state as observed."""

    def __init__(self, exclusivity_key: str) -> None:
        self._exclusivity_key = exclusivity_key
        self._lock = RLock()
        self._binding: InstrumentBindingKey | None = None
        self._description: InstrumentDescription | None = None
        self._endpoint: InstrumentBackendEndpoint | None = None
        self._handle: InstrumentHandle | None = None
        self._owned: OwnedInstrument | None = None
        self._state_cache: _InstrumentStateCache | None = None
        self._epoch = 0
        self._connection_count = 0
        self._shutdown = False

    def acquire(
        self,
        *,
        instrument_id: str,
        binding: InstrumentBindingKey,
        owner: InstrumentOwnerKey,
        endpoint: InstrumentBackendEndpoint,
        connect: InstrumentConnector,
    ) -> OwnedInstrument:
        with self._lock:
            if self._shutdown:
                raise InstrumentActorShutdown(
                    f"instrument actor is shut down: {self._exclusivity_key}"
                )
            if self._owned is not None:
                if self._binding != binding or self._endpoint is not endpoint:
                    raise InstrumentActorConflict(
                        "owned instrument cannot change backend binding"
                    )
                raise InstrumentActorConflict(
                    f"instrument is already owned: {self._exclusivity_key}"
                )
            if self._handle is not None and (
                self._binding != binding or self._endpoint is not endpoint
            ):
                self._disconnect_idle()
            reused_connection = self._handle is not None
            if self._handle is None:
                connected = connect()
                self._connection_count += 1
                self._endpoint = endpoint
                self._handle = connected.handle
                self._description = connected.description
                self._binding = binding
            description = self._description
            assert description is not None
            self._epoch += 1
            owned = OwnedInstrument(
                self,
                instrument_id=instrument_id,
                binding=binding,
                owner=owner,
                epoch=self._epoch,
                description=description,
                reused_connection=reused_connection,
                connection_generation=sha256(
                    f"{self._handle.endpoint_id}:{self._handle.token}".encode()
                ).hexdigest(),
                connection_context=(
                    "warm"
                    if reused_connection
                    else "reconnect"
                    if self._connection_count > 1
                    else "cold"
                ),
            )
            self._owned = owned
            self._state_cache = _InstrumentStateCache(instrument_id)
            return owned

    def read_state(
        self,
        owned: OwnedInstrument,
        request: BackendReadRequest,
    ) -> InstrumentStateReadback:
        with self._lock:
            endpoint, handle = self._require_owned(owned)
            cache = self._require_cache()
            try:
                readback = endpoint.read_state(handle, request)
            except Exception:
                cache.mark(
                    request.targets,
                    status="unknown",
                    reason="state_read_failed",
                )
                raise
            cache.mark(
                request.targets,
                status="unknown",
                reason="state_read_unconfirmed",
            )
            return readback

    def assumed_state(
        self,
        owned: OwnedInstrument,
    ) -> InstrumentStateSnapshot | None:
        with self._lock:
            if self._owned is not owned or owned.epoch != self._epoch:
                return None
            cache = self._state_cache
            return None if cache is None else cache.snapshot()

    def state_cache(
        self,
        owned: OwnedInstrument,
        targets: Iterable[StateMemberTarget],
    ) -> InstrumentStateCacheReadback:
        with self._lock:
            self._require_owned(owned)
            return self._require_cache().read(targets)

    def adopt_state(
        self,
        owned: OwnedInstrument,
        state: InstrumentStateSnapshot,
    ) -> None:
        with self._lock:
            self._require_owned(owned)
            self._require_cache().replace(state)

    def adopt_readback(
        self,
        owned: OwnedInstrument,
        readback: InstrumentStateReadback,
    ) -> None:
        with self._lock:
            self._require_owned(owned)
            self._require_cache().merge(readback)

    def invalidate_state(self, owned: OwnedInstrument) -> None:
        with self._lock:
            self._require_owned(owned)
            self._require_cache().mark_all_unknown("explicit_invalidation")

    def apply_state(
        self,
        owned: OwnedInstrument,
        request: BackendApplyRequest,
    ) -> ApplyReceipt:
        with self._lock:
            endpoint, handle = self._require_owned(owned)
            cache = self._require_cache()
            targets = tuple(assignment.target for assignment in request.assignments)
            try:
                receipt = endpoint.apply_state(handle, request)
            except Exception:
                cache.mark(
                    targets,
                    status="unknown",
                    reason="apply_outcome_unknown",
                )
                raise
            if receipt.status == "applied":
                cache.mark(
                    targets,
                    status="invalidated",
                    reason="state_applied",
                )
            return receipt

    def invoke(
        self,
        owned: OwnedInstrument,
        request: BackendInvokeRequest,
    ) -> InvokeReceipt:
        with self._lock:
            endpoint, handle = self._require_owned(owned)
            cache = self._require_cache()
            invalidated = operation_invalidated_state_members(
                owned.description,
                interface_id=request.interface_id,
                component_path=request.component_path,
                operation_id=request.operation_id,
            )
            targets = tuple(state_member_target(target) for target in invalidated)
            try:
                receipt = endpoint.invoke(handle, request)
            except Exception:
                cache.mark(
                    targets,
                    status="unknown",
                    reason="invoke_outcome_unknown",
                )
                raise
            if receipt.status == "invoked":
                cache.mark(
                    targets,
                    status="invalidated",
                    reason="operation_invalidated",
                )
            return receipt

    def collect(
        self,
        owned: OwnedInstrument,
        request: BackendCollectRequest,
    ) -> CollectReceipt:
        with self._lock:
            endpoint, handle = self._require_owned(owned)
            try:
                receipt = endpoint.collect(handle, request)
            except Exception:
                self._require_cache().mark_all_unknown("collect_outcome_unknown")
                raise
            if receipt.status != "collected":
                self._require_cache().mark_all_unknown("collect_outcome_unknown")
            return receipt

    def prepare_acquisitions(
        self,
        owned: OwnedInstrument,
        plan: BackendAcquisitionPlan,
    ) -> AcquisitionPreparationReceipt:
        with self._lock:
            endpoint, handle = self._require_owned(owned)
            return endpoint.prepare_acquisitions(handle, plan)

    def abort(self, owned: OwnedInstrument) -> None:
        with self._lock:
            endpoint, handle = self._require_owned(owned)
            self._require_cache().mark_all_unknown("aborted")
            endpoint.abort(handle)

    def release(self, owned: OwnedInstrument) -> None:
        with self._lock:
            self._require_owned(owned)
            self._owned = None
            self._state_cache = None
            self._epoch += 1

    def fault(self, owned: OwnedInstrument) -> None:
        with self._lock:
            self._require_owned(owned)
            self._owned = None
            self._epoch += 1
            self._state_cache = None
            connection = self._detach_connection()
            if connection is not None:
                self._disconnect(connection)

    def retire_idle(self) -> None:
        """Permanently close this actor without disturbing a published owner."""

        with self._lock:
            if self._shutdown:
                raise InstrumentActorShutdown(
                    f"instrument actor is shut down: {self._exclusivity_key}"
                )
            if self._owned is not None:
                raise InstrumentActorConflict(
                    f"owned instrument cannot be retired: {self._exclusivity_key}"
                )
            self._shutdown = True
            self._state_cache = None
            self._epoch += 1
            connection = self._detach_connection()
            if connection is not None:
                self._disconnect(connection)

    def shutdown(self) -> None:
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
            if self._owned is not None:
                self._state_cache = None
                self._owned = None
                self._epoch += 1
            connection = self._detach_connection()
            if connection is not None:
                self._disconnect(connection)

    def _require_owned(self, owned: OwnedInstrument) -> InstrumentConnection:
        if (
            self._owned is not owned
            or owned.epoch != self._epoch
            or self._endpoint is None
            or self._handle is None
        ):
            raise InstrumentActorConflict(
                f"stale instrument ownership handle: {self._exclusivity_key}"
            )
        return self._endpoint, self._handle

    def _require_cache(self) -> _InstrumentStateCache:
        cache = self._state_cache
        if cache is None:
            raise InstrumentActorConflict(
                f"instrument state cache is unavailable: {self._exclusivity_key}"
            )
        return cache

    def _disconnect_idle(self) -> None:
        connection = self._detach_connection()
        self._epoch += 1
        if connection is not None:
            self._disconnect(connection)

    def _disconnect(self, connection: InstrumentConnection) -> None:
        endpoint, handle = connection
        try:
            endpoint.disconnect(handle)
        except Exception:
            # A failed disconnect must never be followed by a second connection.
            self._shutdown = True
            raise

    def _detach_connection(self) -> InstrumentConnection | None:
        endpoint = self._endpoint
        handle = self._handle
        self._endpoint = None
        self._handle = None
        self._description = None
        self._binding = None
        if endpoint is None or handle is None:
            return None
        return endpoint, handle


class InstrumentActorRetirement:
    """A scoped per-key acquisition gate for an inventory migration."""

    __slots__ = (
        "_lock",
        "_release_gate_action",
        "_released",
        "_retire_idle_action",
    )

    def __init__(
        self,
        *,
        retire_idle: Callable[[], None],
        release_gate: Callable[[], None],
    ) -> None:
        self._retire_idle_action = retire_idle
        self._release_gate_action = release_gate
        self._lock = RLock()
        self._released = False

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        del exc_type, exc_value, traceback
        self.release_gate()

    def retire_idle(self) -> None:
        """Disconnect and remove every actor after pre-gate acquires drain."""

        with self._lock:
            if self._released:
                raise InstrumentActorConflict("instrument retirement gate is released")
            self._retire_idle_action()

    def release_gate(self) -> None:
        """Allow acquisitions again; repeated release is harmless."""

        with self._lock:
            if self._released:
                return
            self._release_gate_action()
            self._released = True


class InstrumentActorRegistry:
    """Own one actor per exclusive resource and fence lifecycle transitions."""

    def __init__(self) -> None:
        self._actors: dict[str, _InstrumentActor] = {}
        self._previously_connected: set[str] = set()
        self._lock = RLock()
        self._condition = Condition(self._lock)
        self._acquiring: dict[str, int] = {}
        self._retirements: dict[str, object] = {}
        self._accepting = True
        self._closed = False

    def acquire(
        self,
        exclusivity_key: str,
        instrument_id: str,
        *,
        binding: InstrumentBindingKey,
        owner: InstrumentOwnerKey,
        endpoint: InstrumentBackendEndpoint,
        connect: InstrumentConnector,
    ) -> OwnedInstrument:
        with self._condition:
            if not self._accepting:
                raise InstrumentActorShutdown("instrument actor registry is shut down")
            if exclusivity_key in self._retirements:
                raise InstrumentActorConflict(
                    f"instrument actor is retiring: {exclusivity_key}"
                )
            actor = self._actors.setdefault(
                exclusivity_key,
                _InstrumentActor(exclusivity_key),
            )
            self._acquiring[exclusivity_key] = (
                self._acquiring.get(exclusivity_key, 0) + 1
            )
        try:
            owned = actor.acquire(
                instrument_id=instrument_id,
                binding=binding,
                owner=owner,
                endpoint=endpoint,
                connect=connect,
            )
        except BaseException:
            self._finish_acquire(exclusivity_key)
            raise

        with self._condition:
            accepting = self._accepting
            retiring = exclusivity_key in self._retirements
            current = self._actors.get(exclusivity_key) is actor
            if accepting and not retiring and current:
                # Measurement context survives explicit idle-actor retirement,
                # but makes no claim about an earlier daemon process.
                if (
                    owned.connection_context == "cold"
                    and exclusivity_key in self._previously_connected
                ):
                    owned.connection_context = "reconnect"
                self._previously_connected.add(exclusivity_key)
                self._finish_acquire_locked(exclusivity_key)
                return owned

        # A lifecycle gate may close during a slow connection. The handle was
        # never published, so discard it before allowing retirement to proceed.
        try:
            with suppress(InstrumentActorConflict):
                owned.fault()
        finally:
            self._finish_acquire(exclusivity_key)
        if not accepting:
            raise InstrumentActorShutdown("instrument actor registry is shut down")
        raise InstrumentActorConflict(
            f"instrument actor is retiring: {exclusivity_key}"
        )

    def begin_retirement(
        self,
        keys: Iterable[str],
    ) -> InstrumentActorRetirement:
        """Fence a non-empty set of keys until its retirement token is released."""

        selected = tuple(dict.fromkeys(keys))
        if not selected or any(not key for key in selected):
            raise ValueError("instrument retirement keys must be non-empty")
        marker = object()
        token = InstrumentActorRetirement(
            retire_idle=lambda: self._retire_idle(selected, marker),
            release_gate=lambda: self._release_retirement(selected, marker),
        )
        with self._condition:
            if not self._accepting:
                raise InstrumentActorShutdown("instrument actor registry is shut down")
            conflicts = tuple(key for key in selected if key in self._retirements)
            if conflicts:
                raise InstrumentActorConflict(
                    f"instrument actor is already retiring: {', '.join(conflicts)}"
                )
            self._retirements.update(dict.fromkeys(selected, marker))
        return token

    def _retire_idle(
        self,
        keys: tuple[str, ...],
        marker: object,
    ) -> None:
        for exclusivity_key in keys:
            with self._condition:
                while (
                    self._retirements.get(exclusivity_key) is marker
                    and self._acquiring.get(exclusivity_key, 0) != 0
                ):
                    self._condition.wait()
                if self._retirements.get(exclusivity_key) is not marker:
                    raise InstrumentActorConflict(
                        "instrument retirement gate is released"
                    )
                actor = self._actors.get(exclusivity_key)
            if actor is None:
                continue
            actor.retire_idle()
            with self._condition:
                if self._retirements.get(exclusivity_key) is not marker:
                    raise InstrumentActorConflict(
                        "instrument retirement gate is released"
                    )
                if self._actors.get(exclusivity_key) is not actor:
                    raise InstrumentActorConflict(
                        "instrument actor changed during retirement"
                    )
                del self._actors[exclusivity_key]

    def _release_retirement(
        self,
        keys: tuple[str, ...],
        marker: object,
    ) -> None:
        with self._condition:
            for exclusivity_key in keys:
                if self._retirements.get(exclusivity_key) is marker:
                    del self._retirements[exclusivity_key]
            self._condition.notify_all()

    def _finish_acquire(self, exclusivity_key: str) -> None:
        with self._condition:
            self._finish_acquire_locked(exclusivity_key)

    def _finish_acquire_locked(self, exclusivity_key: str) -> None:
        count = self._acquiring[exclusivity_key]
        if count == 1:
            del self._acquiring[exclusivity_key]
        else:
            self._acquiring[exclusivity_key] = count - 1
        self._condition.notify_all()

    def stop_accepting(self) -> None:
        """Fence new owners before the service starts draining durable claims."""

        with self._lock:
            self._accepting = False

    def shutdown(self) -> None:
        with self._lock:
            self._accepting = False
            if self._closed:
                return
            self._closed = True
            actors = tuple(self._actors.values())
        errors: list[Exception] = []
        for actor in actors:
            try:
                actor.shutdown()
            except Exception as error:
                errors.append(error)
        if errors:
            raise ExceptionGroup(
                "instrument actor shutdown failed",
                errors,
            )


__all__ = [
    "InstrumentActorConflict",
    "InstrumentActorError",
    "InstrumentActorRegistry",
    "InstrumentActorRetirement",
    "InstrumentActorShutdown",
    "InstrumentBindingKey",
    "InstrumentOwnerKey",
    "OwnedInstrument",
]
